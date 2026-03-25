"""
FileStorage 实现 - 本地文件存储。

数据面：read/write/get_keys
控制面：step/load_partition/split_input/write_file_path/file_exists

职责：与本地文件系统沟通，负责 bytes 的读写
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Literal, Generator

import pandas as pd
from tqdm import tqdm

from dataflow.logger import get_logger

from .iface import PartitionableStorage, MediaStorage, CacheStorage, ProgressInfo
from .data_parser import get_parser, DataParser


class FileStorage(PartitionableStorage):
    """本地文件存储实现。

    特性：
    - 直接落盘，无内存缓冲
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
        first_entry_file_name: str,
        id_key: str = "id",
        cache_path: str = "./cache",
        cache_type: Literal["json", "jsonl", "csv", "parquet", "pickle"] = "jsonl",
    ):
        """
        初始化 FileStorage。

        Args:
            first_entry_file_name: 第一个输入文件路径
            id_key: 用于合并多步骤数据的唯一键字段名
            cache_path: 缓存目录
            cache_type: 文件格式
        """
        self.logger = get_logger()
        self.files = [first_entry_file_name]  # 输入文件列表
        self.cache_path = cache_path
        self.cache_type = cache_type
        self.id_key = id_key

        # 获取对应的解析器
        self._parser: DataParser = get_parser(cache_type)

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
            self.logger.info(f"📂 读取所有输入文件 (未分片): {len(self.files)} 个")
            df = pd.DataFrame(list(self._read_all()))
        else:
            file_path = self._get_cache_file_path(self.operator_step)
            self.logger.debug(f"📖 读取文件：{file_path}")
            with open(file_path, "rb") as f:
                data = f.read()
            df = self._parser.parse_to_dataframe(data)

        return df if output_type == "dataframe" else df.to_dict(orient="records")

    def write(self, data: Any) -> str:
        """数据面：Operator 调用，写入当前上下文数据。

        写入位置由 operator_step + 1 和 batch_step 共同决定。

        Args:
            data: 要写入的数据（DataFrame 或 List[dict]）

        Returns:
            写入的文件路径
        """
        file_path = self._get_cache_file_path(self.operator_step + 1)
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, pd.DataFrame):
            df = data
        else:
            raise ValueError(f"Unsupported data type: {type(data)}")

        bytes_data = self._parser.serialize_from_dataframe(df)
        with open(file_path, "wb") as f:
            f.write(bytes_data)

        self.logger.info(f"💾 写入 {len(df)} 行数据：{file_path}")
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

    def step(self) -> "FileStorage":
        """控制面：推进到下一个 operator 步骤。

        返回副本，operator_step + 1。
        """
        self.operator_step += 1
        return copy.copy(self)

    def split_input(self, num_partitions: int) -> list[str]:
        """控制面：将输入数据分割成多个分片并存储。

        从 files[0] 读取数据，按 num_partitions 分割后存储到 cache/step_00000000/ 目录。
        分割后更新 self.files 为分片路径列表，供后续 batch_step 遍历使用。

        Args:
            num_partitions: 分片数量

        Returns:
            分片文件的存储路径列表

        Note:
            如果分片文件已存在，会跳过写入（断点续传支持）
        """
        self.logger.info(f"📦 分割输入数据为 {num_partitions} 个分片...")
        total = sum(1 for _ in self._read_all())
        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 总行数：{total}, 每片大小：{self._batch_size}")

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
            if os.path.exists(partition_path):
                self.logger.info(
                    f"Partition {i+1} already exists, skipping: {partition_path}"
                )
                continue

            os.makedirs(os.path.dirname(partition_path) or ".", exist_ok=True)
            bytes_data = self._parser.serialize_from_dataframe(pd.DataFrame(part))
            with open(partition_path, "wb") as f:
                f.write(bytes_data)
            self.logger.debug(f"✅ 创建分片 {i+1}: {partition_path}")

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
            bs = open(self._get_cache_file_path(operator_step), "rb").read()
            for d in self._parser.parse_to_dataframe(bs).to_dict("records"):
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
        return os.path.exists(file_path)

    # ---------- 内部方法 ----------

    def _get_cache_file_path(self, operator_step: int) -> str:
        """
        生成缓存文件路径。

        规则：
        - operator_step == 0 且已分片：返回 files[batch_step]
        - operator_step > 0: 返回 cache/step_{N:08}/part_{M:08}.{ext}

        注意：operator_step == 0 且未分片时，此方法不会被调用（read() 直接调用 _read_all）
        """
        if operator_step == 0:
            return self.files[self._batch_step]

        return os.path.join(
            self.cache_path,
            f"step_{operator_step:08}",
            f"part_{self._batch_step + 1:08}.{self.cache_type}",
        )

    def _get_partition_file_path(self, partition: int) -> str:
        """生成分片文件路径（cache/step_00000000/ 目录）。"""
        return os.path.join(
            self.cache_path,
            "step_00000000",
            f"part_{partition:08}.{self.cache_type}",
        )

    def _read_all(self) -> Generator[dict, None, None]:
        """读取所有输入文件的数据。"""
        for file_path in tqdm(self.files, desc="reading files..."):
            with open(file_path, "rb") as f:
                content = f.read()
            df = self._parser.parse_to_dataframe(content)
            for row in df.to_dict("records"):
                yield row


class FileMediaStorage(MediaStorage):
    """本地文件媒体存储实现。

    从本地文件系统读取媒体文件，适用于本地开发和测试。
    """

    def __init__(self) -> None:
        pass

    def read_media_bytes(self, media_path: str) -> bytes:
        """从本地文件读取媒体数据。

        Args:
            media_path: 本地文件路径

        Returns:
            bytes: 文件的字节内容
        """
        return open(media_path, "rb").read()


class FileCacheStorage(CacheStorage):
    """基于本地文件的进度存储实现

    将进度信息以 JSON 格式保存到本地文件。适用于单机运行场景。

    Attributes:
        cache_file: 缓存文件的路径
    """

    def __init__(self, cache_file: str) -> None:
        """初始化本地文件缓存

        Args:
            cache_file: 缓存文件的完整路径
        """
        self.cache_file = cache_file

    def record_progress(self, progress: ProgressInfo):
        """记录进度到本地 JSON 文件

        Args:
            progress: ProgressInfo 类型的进度对象
        """
        utc_now = datetime.now(timezone.utc)
        iso_string_utc = utc_now.isoformat()
        progress["last_update"] = iso_string_utc
        if progress["start_time"] is None:
            progress["start_time"] = iso_string_utc
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)

    def get_progress(self) -> ProgressInfo:
        """从本地 JSON 文件读取进度

        Returns:
            ProgressInfo 类型的进度对象。如果文件不存在，返回空字典 {}
        """
        if not Path(self.cache_file).exists():
            return {}

        with open(self.cache_file, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
