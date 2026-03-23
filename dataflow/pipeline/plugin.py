import json

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, Optional, List

from dataflow.utils.s3_plugin import (
    exists_s3_object,
    get_s3_client,
    read_s3_bytes,
    split_s3_path,
)


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
    start_time: Optional[str]  # 开始时间 (ISO 8601 格式)
    last_update: Optional[str]  # 最后更新时间 (ISO 8601 格式)
    overall_status: Optional[
        str
    ]  # 整体状态：'running' | 'paused' | 'completed' | 'failed'
    error_message: Optional[str]  # 整体错误信息
    extra: Optional[dict]  # 额外自定义信息


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
        if "start_time" not in progress:
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
        bucket_name, object_key = split_s3_path(self.cache_file)
        utc_now = datetime.now(timezone.utc)
        iso_string_utc = utc_now.isoformat()
        progress["last_update"] = iso_string_utc
        if "start_time" not in progress:
            progress["start_time"] = iso_string_utc
        json_body = json.dumps(progress, ensure_ascii=False, indent=2)
        _ = self.client.put_object(
            Bucket=bucket_name,
            Key=object_key,
            Body=json_body,
        )

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
