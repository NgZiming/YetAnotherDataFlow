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

from .iface import PartitionableStorage, MediaStorage, CacheStorage, ProgressInfo
from .data_parser import get_parser, DataParser

from dataflow.utils.s3_plugin import (
    exists_s3_object,
    get_s3_client,
    list_s3_objects_detailed,
    put_s3_object,
    read_s3_bytes,
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
        endpoint: str,
        ak: str,
        sk: str,
        s3_paths: list[str],
        output_s3_path: str,
        id_key: str,
        cache_type: Literal["json", "jsonl", "csv", "parquet", "pickle"] = "jsonl",
    ):
        """
        初始化 S3Storage。

        Args:
            endpoint: S3 服务端点
            ak: Access Key ID
            sk: Secret Access Key
            s3_paths: 输入 S3 路径列表（可以是文件或目录）
            output_s3_path: 输出 S3 路径
            id_key: 用于合并多步骤数据的唯一键字段名
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

        # 展开输入路径（支持目录）
        client = get_s3_client(endpoint, ak, sk)
        self.files: list[str] = []
        for x in s3_paths:
            if x.endswith("/"):
                for y, _ in list_s3_objects_detailed(client, x, recursive=True):
                    self.files.append(y)
            else:
                self.files.append(x)
        self.files = sorted(list(set(self.files)))

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

        读取优先级：
        1. _current_chunk (预加载的数据块)
        2. operator_step == 0 且未分片：读取所有 files 并合并
        3. operator_step == 0 且已分片：读取 files[batch_step]
        4. operator_step > 0：读取对应步骤的分片文件

        Args:
            output_type: 输出类型，"dataframe" 或 "dict"

        Returns:
            读取的数据（DataFrame 或 List[dict]）
        """
        if self._current_chunk is not None:
            df = self._current_chunk
        elif self.operator_step == 0 and not self._is_partitioned:
            self.logger.info(f"Reading all input files (unpartitioned): {self.files}")
            df = pd.DataFrame(list(self._read_all()))
        else:
            file_path = self._get_cache_file_path(self.operator_step)
            self.logger.info(f"Reading S3 file: {file_path}")
            file_bytes = self._read_file_bytes(file_path)
            df = self._parser.parse_to_dataframe(file_bytes)

        return df if output_type == "dataframe" else df.to_dict(orient="records")

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
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=f".{self.cache_type}"
        ) as tmp:
            self._parser.serialize_to_file(df, tmp)
            tmp_path = tmp.name

        try:
            # 流式上传到 S3
            client = get_s3_client(self.endpoint, self.ak, self.sk)
            with open(tmp_path, "rb") as f:
                put_s3_object(client, file_path, f)
            self.logger.info(f"Wrote {len(df)} rows to S3: {file_path}")
        finally:
            os.unlink(tmp_path)
        return file_path

    def get_keys(self) -> list[str]:
        """数据面：Operator 调用，获取字段名。"""
        df = self.read(output_type="dataframe")
        return df.columns.tolist() if isinstance(df, pd.DataFrame) else []

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

    @property
    def current_chunk(self) -> Optional[pd.DataFrame]:
        return self._current_chunk

    @current_chunk.setter
    def current_chunk(self, value: Optional[pd.DataFrame]) -> None:
        self._current_chunk = value

    def step(self) -> "S3Storage":
        """控制面：推进到下一个 operator 步骤。

        返回副本，operator_step + 1。
        """
        self.operator_step += 1
        return copy.copy(self)

    def split_input(self, num_partitions: int) -> list[str]:
        """控制面：将输入数据分割成多个分片并存储到 S3。

        从 files[0] 读取数据，按 num_partitions 分割后存储到 output_s3_path/step_00000000/ 目录。
        分割后更新 self.files 为分片路径列表，供后续 batch_step 遍历使用。

        Args:
            num_partitions: 分片数量

        Returns:
            分片文件的 S3 路径列表

        Note:
            如果分片文件已存在，会跳过写入（断点续传支持）
        """
        self.logger.info(f"📦 分割 S3 输入数据为 {num_partitions} 个分片...")
        total = sum(1 for _ in self._read_all())
        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 总行数：{total}, 每片大小：{self._batch_size}")

        client = get_s3_client(self.endpoint, self.ak, self.sk)
        partition_paths = []
        rows = list(self._read_all())

        for i in tqdm(
            range(num_partitions),
            total=num_partitions,
            desc="Creating partitions",
        ):
            part: list[dict] = []
            for row in rows:
                part.append(row)
                if len(part) >= self._batch_size:
                    break

            partition_path = self._get_partition_file_path(i + 1)
            partition_paths.append(partition_path)
            if self.file_exists(partition_path):
                self.logger.info(
                    f"Partition {i+1} already exists, skipping: {partition_path}"
                )
                continue

            # 先写临时文件，再上传 S3
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix=f".{self.cache_type}"
            ) as tmp:
                self._parser.serialize_to_file(pd.DataFrame(part), tmp)
                tmp_path = tmp.name

            try:
                client = get_s3_client(self.endpoint, self.ak, self.sk)
                with open(tmp_path, "rb") as f:
                    put_s3_object(client, partition_path, f)
                self.logger.info(f"Created partition {i+1}: {partition_path}")
            finally:
                os.unlink(tmp_path)

        self.files = partition_paths
        self._is_partitioned = True
        self.logger.info(
            f"Split input complete. {len(partition_paths)} partitions created."
        )
        return partition_paths

    def load_partition(self, dependent_steps: list[int]) -> pd.DataFrame:
        """控制面：加载指定分片的数据，并合并依赖步骤的结果。

        核心逻辑：
        1. 遍历 dependent_steps 中的每个步骤
        2. 从每个步骤加载数据，按 id_key 去重合并
        3. 取所有步骤的 id_key 交集
        4. 返回交集数据

        Args:
            dependent_steps: 依赖的前驱步骤列表

        Returns:
            合并后的 DataFrame（只包含所有步骤的交集数据）
        """
        ds: dict[str, dict] = {}  # id_key -> 合并后的记录
        kept_keys: list[set] = []  # 每个步骤的 id_key 集合
        for operator_step in dependent_steps:
            kept_keys.append(set())
            # 读取指定步骤的文件
            file_path = self._get_cache_file_path(operator_step)
            content = self._read_file_bytes(file_path)
            for d in self._parser.parse_to_dataframe(content).to_dict("records"):
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

        return pd.DataFrame(list(ds.values()))

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

        注意：operator_step == 0 且未分片时，此方法不会被调用（read() 直接调用 _read_all）
        """
        if operator_step == 0:
            return self.files[self._batch_step]

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

    def _read_all(self) -> Generator[dict, None, None]:
        """读取所有输入文件的数据。"""
        for s3_path in tqdm(self.files, desc="reading file..."):
            content = self._read_file_bytes(s3_path)
            df = self._parser.parse_to_dataframe(content)
            for row in df.to_dict("records"):
                yield row

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
        self.logger.info(f"Read {len(content)} bytes from: {s3_path}")
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
        return read_s3_bytes(get_s3_client(self.endpoint, self.ak, self.sk), media_path)


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

        json_body = read_s3_bytes(self.client, self.cache_file).decode("utf-8")
        try:
            return json.loads(json_body)
        except:
            return {}
