"""
Storage 接口定义模块。

采用数据面/控制面分离设计：
- 数据面 (StorageABC): 给 Operator 使用，只关心数据读写
- 控制面 (PartitionableStorage): 给 Pipeline 控制层使用，管理分片/批次/并发
- DataSource: 数据源抽象类，负责从各种数据源读取数据

核心设计：
- files: 输入文件列表，files[batch_step] 是当前分片的输入文件
- operator_step: 算子步骤索引，决定输出到第几步
- batch_step: 批次/分片索引，决定处理哪个分片
- id_key: 用于合并多步骤数据的唯一键
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, Literal, TypedDict, List, Generator
from dataclasses import dataclass
import uuid

import pandas as pd


# ==================== 数据源抽象类 ====================


@dataclass
class DataSourceInfo:
    """数据源信息

    Attributes:
        source_type: 数据源类型（"s3", "file", "huggingface", "modelscope"）
        path: 数据源路径
        num_files: 文件数量
        total_rows: 总行数（如果已知）
        schema: 字段名列表
    """

    source_type: str
    paths: list[str]


class DataSource(ABC):
    """数据源抽象基类

    职责：从各种数据源读取数据（只读），不负责存储
    支持：S3、本地文件、HuggingFace、ModelScope 等

    使用示例:
        # S3 数据源
        s3_source = S3DataSource(
            endpoint="https://s3.example.com",
            ak="xxx", sk="xxx",
            s3_path="s3://bucket/dataset/"
        )

        # HuggingFace 数据源
        hf_source = HuggingFaceDataSource(
            dataset="username/dataset_name",
            split="train"
        )

        # 在 Pipeline 中使用
        storage = FileStorage("output.jsonl")
        storage.split_input(data_source=s3_source, num_partitions=10)
    """

    @abstractmethod
    def get_info(self) -> DataSourceInfo:
        """获取数据源信息

        Returns:
            数据源元数据（类型、路径、文件数、行数、schema 等）
        """
        pass

    @abstractmethod
    def estimate_total_rows(self) -> int:
        """估算总行数（无需完整读取数据）

        不同数据源采用不同策略：
        - 本地文件：采样估算或文件大小/平均每行
        - S3：对象元数据估算
        - HF/MS：数据集 API 直接返回

        Returns:
            估算的总行数（必须 >= 1）
        """
        pass

    @abstractmethod
    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        """流式读取数据

        Args:
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录
        """
        pass


# ==================== ID 合成器抽象类 ====================


class IdSynthesizer(ABC):
    """ID 合成器抽象基类

    职责：为缺少 id_key 的数据行合成唯一 ID。
    在 split_input() 时调用，检查每行数据是否包含 id_key，
    如果不存在则使用 synthesizer 生成一个。

    使用示例:
        # 使用 UUID 合成器
        synthesizer = UuidIdSynthesizer(prefix="row")
        storage = FileStorage(data_source=source, id_key="id", id_synthesizer=synthesizer)

        # 自定义合成器
        class CustomIdSynthesizer(IdSynthesizer):
            def __init__(self, prefix: str):
                self.prefix = prefix
                self.counter = 0

            def synthesize(self, row: dict, row_index: int) -> str:
                self.counter += 1
                return f"{self.prefix}_{self.counter}"

        synthesizer = CustomIdSynthesizer("custom")
    """

    @abstractmethod
    def synthesize(self, row: dict, row_index: int) -> str:
        """为单行数据合成唯一 ID。

        Args:
            row: 数据行（dict）
            row_index: 行索引（从 0 开始，可用于调试或生成可追溯的 ID）

        Returns:
            合成的唯一 ID 字符串
        """
        pass


class UuidIdSynthesizer(IdSynthesizer):
    """基于 UUID 的 ID 合成器

    生成格式：{prefix}-{uuid}，例如 "row-a1b2c3d4-e5f6-..."

    Attributes:
        prefix: ID 前缀，默认为 "row"
    """

    def __init__(self, prefix: str = "row"):
        """
        Args:
            prefix: ID 前缀
        """
        self.prefix = prefix

    def synthesize(self, row: dict, row_index: int) -> str:
        """生成 UUID 格式的 ID。

        Args:
            row: 数据行（未使用，但保留参数以符合接口）
            row_index: 行索引（未使用，但保留参数以符合接口）

        Returns:
            格式为 "{prefix}-{uuid}" 的唯一 ID
        """
        return f"{self.prefix}-{uuid.uuid4()}"


class CounterIdSynthesizer(IdSynthesizer):
    """基于计数器的 ID 合成器

    生成格式：{prefix}_{递增数字}，例如 "row_0", "row_1", ...

    注意：此合成器有状态，多线程/并发场景下可能不安全。

    Attributes:
        prefix: ID 前缀，默认为 "row"
        start: 起始数字，默认为 0
    """

    def __init__(self, prefix: str = "row", start: int = 0):
        """
        Args:
            prefix: ID 前缀
            start: 起始数字
        """
        self.prefix = prefix
        self._counter = start

    def synthesize(self, row: dict, row_index: int) -> str:
        """生成递增数字格式的 ID。

        Args:
            row: 数据行（未使用，但保留参数以符合接口）
            row_index: 行索引（用于同步计数器，但实际使用内部计数器）

        Returns:
            格式为 "{prefix}_{counter}" 的唯一 ID
        """
        result = f"{self.prefix}_{self._counter:08}"
        self._counter += 1
        return result


# ==================== 数据面接口（Data Plane） ====================
# 给 Operator 使用，不涉及分片/并发逻辑


class StorageABC(ABC):
    """基础存储接口 - 数据面。

    Operator 通过此接口读写数据，无需关心外层如何调度分片、批次或并发。
    外层控制面会在调用前设置好上下文（如 batch_step、_current_chunk 等）。

    使用示例:
        def run(self, storage: StorageABC):
            df = storage.read()  # 直接读，不关心分片
            result = self.process(df)
            storage.write(result)  # 直接写
    """

    @abstractmethod
    def read(self, output_type: Literal["dataframe", "dict"] = "dataframe") -> Any:
        """读取当前上下文的数据。

        读取的数据来源由控制面决定：
        - 如果 _current_chunk 已设置，直接返回此数据
        - 否则从当前 operator_step 和 batch_step 对应的文件读取

        Args:
            output_type: 输出类型 ("dataframe" 或 "dict")

        Returns:
            读取的数据（DataFrame 或 List[dict]）

        Example (Operator 视角):
            def run(self, storage: StorageABC):
                df = storage.read()  # 直接读，不关心分片
                result = self.process(df)
                storage.write(result)  # 直接写
        """
        pass

    @abstractmethod
    def write(self, data: Any) -> str:
        """写入数据到当前上下文。

        写入位置由 operator_step 和 batch_step 共同决定。

        Args:
            data: 要写入的数据（DataFrame 或 List[dict]）

        Returns:
            写入的文件路径

        Example (Operator 视角):
            def run(self, storage: StorageABC):
                df = storage.read()
                result = self.process(df)
                path = storage.write(result)  # 返回输出路径
        """
        pass

    @abstractmethod
    def get_keys(self) -> list[str]:
        """获取当前数据的字段名列表。

        Returns:
            排序后的字段名列表

        Example (Operator 视角):
            def run(self, storage: StorageABC):
                keys = storage.get_keys()  # 检查输入字段
        """
        pass


# ==================== 控制面接口（Control Plane） ====================
# 给 Pipeline 控制层使用，管理分片、批次、并发


class PartitionableStorage(StorageABC):
    """支持分片/批处理的存储接口 - 控制面扩展。

    Pipeline 控制层通过此接口管理分片调度、批次处理、断点续传等。

    核心设计：
    - files: 输入文件列表，files[batch_step] 是当前分片的输入
    - operator_step: 算子步骤索引，决定输出到第几步
    - batch_step: 批次/分片索引，决定处理哪个分片
    - _current_chunk: 预加载的数据块，用于优化性能

    文件路径规则：
    - operator_step == 0: files[batch_step]（输入文件）
    - operator_step > 0: output_path/step_{N:08}/part_{M:08}.{ext}

    使用示例:
        storage = FileStorage("input.jsonl")
        for op in operators:
            storage = storage.step()  # 推进步骤
            op.run(storage=storage)
    """

    # ---------- 控制面属性 ----------

    @property
    @abstractmethod
    def batch_size(self) -> Optional[int]:
        """批次大小，None 表示不分批。

        用于计算分片数量：num_partitions = (total_rows + batch_size - 1) // batch_size
        """
        pass

    @property
    @abstractmethod
    def batch_step(self) -> int:
        """当前批次/分片索引（从 0 开始）。

        用于：
        - 从 files[batch_step] 读取输入
        - 决定输出到哪个分片目录
        """
        pass

    @batch_step.setter
    @abstractmethod
    def batch_step(self, value: int) -> None:
        """设置当前批次索引。"""
        pass

    # ---------- 控制面方法 ----------

    @abstractmethod
    def step(self) -> "PartitionableStorage":
        """推进到下一个 operator 步骤。

        返回一个新的 Storage 实例，operator_step + 1。
        Pipeline 调用此方法在算子之间传递 Storage。

        Returns:
            新的 Storage 实例（operator_step + 1）

        Example:
            storage = FileStorage("input.jsonl")
            for op in operators:
                storage = storage.step()  # 推进步骤
                op.run(storage=storage)
        """
        pass

    @abstractmethod
    def load_partition(self, dependent_steps: list[int]) -> pd.DataFrame:
        """加载指定分片的数据，并合并依赖步骤的结果。

        这是并行分片处理的核心方法：
        1. 从 dependent_steps 对应的每个步骤加载数据
        2. 通过 id_key 合并所有步骤的数据（取交集）
        3. 返回合并后的 DataFrame，并设置 self._current_chunk 供 read() 使用

        应用场景：
        - 多步骤并行处理后，需要取交集数据
        - 例如：步骤 0 过滤出 100 条，步骤 1 过滤出 80 条，取交集 60 条

        Args:
            dependent_steps: 依赖的前驱步骤列表

        Returns:
            合并后的分片数据 DataFrame（只包含所有步骤的交集）
            同时设置 self._current_chunk，供后续 read() 调用使用

        Example:
            # 加载第 0 步和第 1 步的交集数据
            df = storage.load_partition(dependent_steps=[0, 1])
        """
        pass

    @abstractmethod
    def split_input(self, num_partitions: int) -> list[str]:
        """将数据源数据分割成多个分片并存储。

        用于 Pipeline 第一次输入数据时的分片处理。
        从 DataSource 读取数据，分割后存储到 output_path/step_00000000/ 目录。
        分割后更新 self.files 为分片路径列表，供后续 batch_step 遍历使用。

        Args:
            data_source: 数据源实例
            num_partitions: 分片数量

        Returns:
            分片数据的存储路径列表

        Example:
            from .iface import create_data_source

            source = create_data_source("s3://bucket/data.jsonl")
            storage = FileStorage("output.jsonl")
            paths = storage.split_input(data_source=source, num_partitions=10)
            # paths = ["cache/step_00000000/00000001.jsonl", ...]
            # 此时 files = paths，batch_step 可以遍历 0~9
        """
        pass

    @abstractmethod
    def write_file_path(self) -> str:
        """生成当前步骤的输出文件路径。

        路径由 operator_step 和 batch_step 共同决定。

        Returns:
            输出文件路径

        Example:
            storage.operator_step = 1
            storage.batch_step = 3
            path = storage.write_file_path()
            # "cache/step_00000002/part_00000004.jsonl"
        """
        pass

    @abstractmethod
    def file_exists(self, file_path: str) -> bool:
        """检查文件是否存在（用于断点续传）。

        Args:
            file_path: 文件路径

        Returns:
            文件存在返回 True

        Example:
            path = storage.write_file_path()
            if storage.file_exists(path):
                print("已处理过，跳过")
        """
        pass


class MediaStorage(ABC):
    """媒体存储抽象基类。

    定义读取媒体文件字节数据的接口，支持不同存储后端。
    """

    @abstractmethod
    def read_media_bytes(self, media_path: str) -> bytes:
        """读取媒体文件的字节数据。

        Args:
            media_path: 媒体文件路径（具体格式取决于实现类）

        Returns:
            bytes: 媒体文件的字节内容

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError


class PartitionOrBatchProgress(TypedDict):
    """单个 partition 或 batch 的进度信息

    每个 partition/batch 独立跟踪进度，可以在不同的 operator 阶段。
    恢复时可以从每个 partition 的确切位置继续，不受其他 partition 影响。

    注意:
    - 状态信息（status/error_message）存储在 ProgressInfo 级别，不在单个 partition 级别
    - 这样可以简化进度更新逻辑，避免状态不一致

    Attributes:
        id: partition 或 batch 的唯一标识 ID
        completed_steps: 已完成的步骤 ID 列表（如 operator 步骤、batch 步骤等）
        current_steps: 当前正在处理的步骤 ID 列表（支持并发，可为多个）
        steps_rows_nums: 每个步骤处理的数据行数列表（与 completed_steps 对应，用于统计数据留存率）
    """

    id: int  # partition 或 batch 的唯一标识 ID
    completed_steps: List[int]  # 已完成的步骤 ID 列表
    current_steps: List[int]  # 当前正在处理的步骤 ID 列表（支持并发）
    steps_rows_nums: dict[int, int]  # 每个步骤处理的数据行数（与 completed_steps 对应）


class ProgressInfo(TypedDict):
    """整体处理进度的类型定义

    用于跟踪整个 pipeline 的处理进度，包含所有 partition/batch 的独立进度。
    total=False 表示所有字段都是可选的，可以根据需要只存储部分信息。

    设计原则:
    - 每个 partition 独立跟踪进度，可以在不同的 operator 阶段
    - 恢复时可以从每个 partition 的确切位置继续
    - 支持并行处理，不同 partition 可以处于不同状态

    Attributes:
        shard_type: 分片类型，'partition' 表示按数据分片，'batch' 表示按 batch 分
        partitions: 所有 partition/batch 的进度列表
        total_shards: 总的 partition 数或 batch 数
        start_time: 处理开始时间（ISO 8601 格式）
        last_update: 最后更新时间（ISO 8601 格式）
        overall_status: 整体状态 'running' | 'paused' | 'completed' | 'failed'
        error_message: 整体错误信息（如果 overall_status='failed'）
        extra: 额外自定义信息，可用于存储特定于业务的元数据
    """

    shard_type: str  # 分片类型：'partition' | 'batch'
    partitions: List[PartitionOrBatchProgress]  # 所有 partition/batch 的进度列表
    total_shards: int  # 总的 partition 数或 batch 数
    total_steps: int  # step 数
    start_time: Optional[str]  # 开始时间 (ISO 8601 格式)
    last_update: Optional[str]  # 最后更新时间 (ISO 8601 格式)
    overall_status: Optional[
        str
    ]  # 整体状态：'running' | 'paused' | 'completed' | 'failed'
    error_message: Optional[str]  # 整体错误信息
    extra: Optional[dict]  # 额外自定义信息
    pipeline_class: str  # pipeline 使用的类
    op_list: List[str]  # pipeline 使用的类


class CacheStorage(ABC):
    """进度存储的抽象基类

    定义进度存储的接口，支持不同的存储后端（本地文件、S3 等）。
    用于在 pipeline 处理过程中持久化进度，支持断点续传。
    """

    @abstractmethod
    def record_progress(self, progress: ProgressInfo):
        """记录处理进度

        将当前进度持久化到存储后端。调用方应在关键检查点调用此方法，
        以确保故障恢复时能回到最近的有效状态。

        Args:
            progress: 包含当前处理进度的 ProgressInfo 对象
        """
        raise NotImplementedError

    @abstractmethod
    def get_progress(self) -> ProgressInfo:
        """获取存储的处理进度

        从存储后端读取之前记录的进度。用于恢复处理时读取断点信息。

        Returns:
            ProgressInfo 类型的进度对象。如果没有之前记录的进度，返回空字典 {}
        """
        raise NotImplementedError
