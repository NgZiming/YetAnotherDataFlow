"""
S3Storage 实现 - S3 对象存储。

数据面：read/write/get_keys
控制面：step/load_partition/split_input/write_file_path/file_exists

职责：与 S3 对象存储沟通，负责 bytes 的读写
文件格式解析委托给 DataParser

核心设计：
- files: 输入文件列表，files[batch_step] 是当前分片的输入文件
- operator_step: 算子步骤索引，决定输出到第几步
- batch_step: 批次/分片索引，决定处理哪个分片
- id_key: 用于合并多步骤数据的唯一键
"""

import copy
import json
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Literal, Generator

import pandas as pd

from botocore.response import StreamingBody
from tqdm import tqdm

from .iface import (
    CacheStorage,
    DataSource,
    MediaStorage,
    PartitionableStorage,
    ProgressInfo,
)
from .data_parser import get_parser, DataParser

from dataflow.utils.s3_plugin import (
    exists_s3_object,
    get_s3_client,
    put_s3_object,
    read_s3_bytes,
    upload_s3_object,
)
from dataflow.logger import get_logger


class S3Storage(PartitionableStorage):
    """S3 对象存储实现。

    特性：
    - 支持 S3 对象存储
    - 文件格式解析委托给 DataParser
    - 支持多文件输入（files 列表）
    - 支持基于 id_key 的多步骤数据合并

    核心属性：
    - files: 输入文件列表，files[batch_step] 是当前分片的输入
    - operator_step: 算子步骤索引，决定输出到第几步
    - batch_step: 批次/分片索引，决定处理哪个分片
    - id_key: 用于合并多步骤数据的唯一键
    """

    def __init__(
        self,
        data_source: DataSource,
        output_s3_path: str,
        id_key: str,
        endpoint: str,
        ak: str,
        sk: str,
        cache_type: Literal["json", "jsonl", "csv", "parquet", "pickle"] = "jsonl",
    ):
        """
        初始化 S3Storage。

        核心设计：
        - files: 输入文件列表，files[batch_step] 是当前分片的输入文件
        - operator_step: 算子步骤索引，决定输出到第几步
        - batch_step: 批次/分片索引，决定处理哪个分片
        - id_key: 用于合并多步骤数据的唯一键
        - _current_chunk: load_partition() 返回的合并数据块

        使用流程：
        1. split_input() - 分片输入数据（必须，即使只分一片）
        2. 处理步骤 0: batch_step=0, read() 直接读 files[0]
        3. 处理步骤 >0: load_partition(), read() 返回 _current_chunk

        Args:
            data_source: 数据源实例（必选）
            output_s3_path: 输出 S3 路径
            id_key: 用于合并多步骤数据的唯一键字段名
            endpoint: S3 服务端点
            ak: Access Key ID
            sk: Secret Access Key
            cache_type: 文件格式
        """
        self.logger = get_logger()
        self.endpoint = endpoint
        self.ak = ak
        self.sk = sk
        self.cache_type = cache_type
        self.id_key = id_key

        # 获取对应的解析器
        self._parser: DataParser = get_parser(cache_type)

        # 数据源（必选）
        self.data_source = data_source

        self.output_s3_path = output_s3_path

        self.operator_step = -1  # 当前算子步骤（-1 表示未开始）

        # 控制面属性
        self._batch_size: Optional[int] = None  # 批次大小
        self._batch_step: int = 0  # 当前批次索引
        self._current_chunk: Optional[pd.DataFrame] = None  # 预加载的数据块

        # 标记是否进行过分片
        self._is_partitioned = False

    # ---------- 数据面实现 ----------

    def read(self, output_type: Literal["dataframe", "dict"] = "dataframe") -> Any:
        """数据面：Operator 调用，读取当前上下文数据。

        核心逻辑：
        - 只返回 _current_chunk（load_partition() 返回的合并数据）
        - 必须先调用 split_input() 进行分片
        - 必须先调用 load_partition() 才能读取

        Args:
            output_type: 输出类型，"dataframe" 或 "dict"

        Returns:
            读取的数据（DataFrame 或 List[dict]）

        Raises:
            RuntimeError: 未调用 split_input() 或 load_partition() 时抛出
        """
        if not self._is_partitioned:
            raise RuntimeError("Must call split_input() before read()")

        if self._current_chunk is None:
            raise RuntimeError("Must call load_partition() before read()")

        return (
            self._current_chunk
            if output_type == "dataframe"
            else self._current_chunk.to_dict(orient="records")
        )

    def write(self, data: Any) -> str:
        """数据面：Operator 调用，写入当前上下文数据。

        写入位置由 operator_step + 1 和 batch_step 共同决定。

        Args:
            data: 要写入的数据（DataFrame 或 List[dict]）

        Returns:
            写入的文件路径
        """
        file_path = self.write_file_path()

        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, pd.DataFrame):
            df = data
        else:
            raise ValueError(f"Unsupported data type: {type(data)}")

        # 先写临时文件，再上传 S3，避免内存缓冲
        tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=f".{self.cache_type}"
        )
        tmp_path = tmp.name
        self._parser.serialize_to_file(df, tmp.name)

        try:
            # 流式上传到 S3
            client = get_s3_client(self.endpoint, self.ak, self.sk)
            upload_s3_object(client, file_path, tmp_path)
            self.logger.info(f"Wrote {len(df)} rows to S3: {file_path}")
        finally:
            os.unlink(tmp_path)
        return file_path

    def get_keys(self) -> list[str]:
        """数据面：Operator 调用，获取字段名。

        优先从 DataSource 获取 schema，如果未提供则读取文件。
        """
        for row in self.data_source.read():
            return sorted(row.keys())
        return []

    # ---------- 控制面实现 ----------

    @property
    def batch_size(self) -> Optional[int]:
        return self._batch_size

    @property
    def batch_step(self) -> int:
        return self._batch_step

    @batch_step.setter
    def batch_step(self, value: int) -> None:
        self._batch_step = value

    def step(self) -> "S3Storage":
        """控制面：推进到下一个 operator 步骤。

        返回副本，operator_step + 1。
        """
        self.operator_step += 1
        return copy.copy(self)

    def split_input(self, num_partitions: int) -> list[str]:
        """控制面：将输入数据分割成多个分片并存储到 S3。

        从 DataSource 读取数据，按 num_partitions 分割后存储到 output_s3_path/step_00000000/ 目录。
        分割后更新 self.files 为分片路径列表，供后续 batch_step 遍历使用。

        使用示例：
            storage = S3Storage(data_source=source, output_s3_path="s3://bucket/output/")
            storage.split_input(num_partitions=10)
            # 此时 files = partition_paths，batch_step 可以遍历 0~9

        Args:
            num_partitions: 分片数量

        Returns:
            分片文件的 S3 路径列表

        Note:
            - 即使只分一片，也必须调用此方法
            - 如果分片文件已存在，会跳过写入（断点续传支持）
        """
        self.logger.info(f"📦 分割输入数据为 {num_partitions} 个分片...")

        # 流式写入：遍历一次 DataSource，分发到各个 partition 文件
        rows = self.data_source.read()
        total = sum(1 for _ in rows)

        if total < num_partitions:
            raise RuntimeError("Rows count less than num of partitions.")

        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 总行数：{total}, 每片大小：{self._batch_size}")

        partition_paths = []
        rows = self.data_source.read()
        for idx in tqdm(
            range(num_partitions),
            total=num_partitions,
            desc="Partitioning",
        ):
            part: list[dict] = []
            for row in tqdm(rows, total=self._batch_size, desc="Reading Rows"):
                part.append(row)
                if len(part) >= self._batch_size:
                    break
            # 写入当前分片
            partition_path = self._get_partition_file_path(idx + 1)
            partition_paths.append(partition_path)

            if self.file_exists(partition_path):
                self.logger.info(f"Partition {idx + 1} already exists, skipping")
            else:
                # 先写临时文件，再上传 S3
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=f".{self.cache_type}"
                )
                tmp_path = tmp.name
                self._parser.serialize_to_file(pd.DataFrame(part), tmp_path)

                try:
                    client = get_s3_client(self.endpoint, self.ak, self.sk)
                    upload_s3_object(client, partition_path, tmp_path)
                    self.logger.info(
                        f"Partition {idx + 1} with {len(part)} rows created."
                    )
                finally:
                    os.unlink(tmp_path)

        self._is_partitioned = True
        self.logger.info(f"Split input complete. {num_partitions} partitions created.")
        return partition_paths

    def load_partition(self, dependent_steps: list[int]) -> pd.DataFrame:
        """控制面：加载指定分片的数据，并合并依赖步骤的结果。

        核心逻辑：
        1. 遍历 dependent_steps 中的每个步骤
        2. 从每个步骤加载数据，按 id_key 去重合并
        3. 取所有步骤的 id_key 交集
        4. 返回交集数据，并设置 self._current_chunk 供 read() 使用

        Args:
            dependent_steps: 依赖的前驱步骤列表

        Returns:
            合并后的 DataFrame（只包含所有步骤的交集数据）
            同时设置 self._current_chunk，供后续 read() 调用使用
        """
        ds: dict[str, dict] = {}  # id_key -> 合并后的记录
        kept_keys: list[set] = []  # 每个步骤的 id_key 集合
        for operator_step in dependent_steps:
            kept_keys.append(set())
            # 读取指定步骤的文件
            file_path = self._get_cache_file_path(operator_step)
            content = self._read_file_bytes(file_path)
            for d in self._parser.parse_to_dataframe(content):
                if d[self.id_key] not in ds:
                    ds[d[self.id_key]] = {}
                ds[d[self.id_key]].update(d)  # 合并记录
                kept_keys[-1].add(d[self.id_key])  # 记录 id_key

        # 取所有步骤的 id_key 交集
        all_keys = kept_keys[0]
        for idx in range(1, len(kept_keys)):
            all_keys = all_keys.intersection(kept_keys[idx])

        # 移除不在交集中的数据
        removed_keys = set(ds.keys()) - all_keys
        for key in removed_keys:
            ds.pop(key)

        result = pd.DataFrame(list(ds.values()))
        self._current_chunk = result  # 直接设置内部变量，供 read() 使用
        return result

    def write_file_path(self) -> str:
        """控制面：生成当前步骤的输出文件路径。"""
        return self._get_cache_file_path(self.operator_step + 1)

    def file_exists(self, file_path: str) -> bool:
        """控制面：检查文件是否存在。"""
        client = get_s3_client(self.endpoint, self.ak, self.sk)
        return exists_s3_object(client, file_path)

    # ---------- 内部方法 ----------

    def _get_cache_file_path(self, operator_step: int) -> str:
        """
        生成缓存文件路径。

        规则：
        - operator_step == 0 且已分片：返回 files[batch_step]
        - operator_step > 0: 返回 output_s3_path/step_{N:08}/part_{M:08}.{ext}

        注意：operator_step == 0 且未分片时，此方法不会被调用
        """
        file_path = Path(self.output_s3_path.removeprefix("s3://")).joinpath(
            f"step_{operator_step:08}",
            f"part_{self._batch_step + 1:08}.{self.cache_type}",
        )
        return "s3://" + str(file_path)

    def _get_partition_file_path(self, partition: int) -> str:
        """生成分片文件路径（output_s3_path/step_00000000/ 目录）。"""
        file_path = Path(self.output_s3_path.removeprefix("s3://")).joinpath(
            "step_00000000",
            f"part_{partition:08}.{self.cache_type}",
        )
        return "s3://" + str(file_path)

    def _read_file_bytes(self, s3_path: str) -> StreamingBody:
        """从 S3 读取文件内容。

        Args:
            s3_path: S3 路径

        Returns:
            StreamingBody: 文件的字节流对象
        """
        self.logger.info(f"Reading S3 file: {s3_path}")
        client = get_s3_client(self.endpoint, self.ak, self.sk)
        content = read_s3_bytes(client, s3_path)
        # self.logger.info(f"Read {len(content)} bytes from: {s3_path}")
        return content


class S3MediaStorage(MediaStorage):
    """S3 媒体存储实现。

    从 S3 读取媒体文件（图片、视频等），适用于多模态数据处理场景。

    Args:
        endpoint: S3 服务端点 URL
        ak: 访问密钥 ID
        sk: 秘密访问密钥

    Example:
        >>> storage = S3MediaStorage("https://s3.example.com", "ak", "sk")
        >>> img_bytes = storage.read_media_bytes("s3://bucket/images/photo.jpg")
    """

    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
    ) -> None:
        self.endpoint = endpoint
        self.ak = ak
        self.sk = sk

    def read_media_bytes(self, media_path: str) -> bytes:
        """从 S3 读取媒体文件。

        Args:
            media_path: S3 路径，格式为 s3://bucket/key

        Returns:
            bytes: 媒体文件的字节内容
        """
        return read_s3_bytes(
            get_s3_client(self.endpoint, self.ak, self.sk), media_path
        ).read()


class S3CacheStorage(CacheStorage):
    """基于 S3 的进度存储实现

    将进度信息以 JSON 格式保存到 S3。适用于分布式或需要远程访问的场景。

    Attributes:
        client: S3 客户端实例
        cache_file: S3 文件路径（格式：s3://bucket/path/to/file）
    """

    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
        cache_file: str,
    ) -> None:
        """初始化 S3 缓存

        Args:
            endpoint: S3 服务端点
            ak: 访问密钥 ID
            sk: 秘密访问密钥
            cache_file: S3 文件路径（格式：s3://bucket/path/to/file）
        """
        super().__init__()
        self.client = get_s3_client(endpoint, ak, sk)
        self.cache_file = cache_file

    def record_progress(self, progress: ProgressInfo):
        """记录进度到 S3 JSON 文件

        Args:
            progress: ProgressInfo 类型的进度对象
        """
        utc_now = datetime.now(timezone.utc)
        iso_string_utc = utc_now.isoformat()
        progress["last_update"] = iso_string_utc
        if progress["start_time"] is None:
            progress["start_time"] = iso_string_utc
        json_body = json.dumps(progress, ensure_ascii=False, indent=2)
        put_s3_object(self.client, self.cache_file, json_body.encode("utf-8"))

    def get_progress(self) -> ProgressInfo:
        """从 S3 JSON 文件读取进度

        Returns:
            ProgressInfo 类型的进度对象。如果文件不存在，返回空字典 {}
        """
        if not exists_s3_object(self.client, self.cache_file):
            return {}

        json_body = read_s3_bytes(self.client, self.cache_file).read()
        try:
            return json.loads(json_body)
        except:
            return {}
