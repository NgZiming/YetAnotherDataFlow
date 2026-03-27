"""
Storage 模块 - 数据面/控制面分离的存储接口。

核心设计：
- 数据面 (StorageABC): Operator 使用，只关心数据读写
- 控制面 (PartitionableStorage): Pipeline 使用，管理分片/批次/并发
- DataSource: 数据源抽象类，从各种数据源读取数据（只读）
- DataParser: 文件格式解析（bytes <-> DataFrame）

导出内容：
- StorageABC: 数据面基础接口
- PartitionableStorage: 控制面扩展接口
- FileStorage/S3Storage: 存储实现
- DataSource/FileDataSource/S3DataSource/HuggingFaceDataSource/ModelScopeDataSource: 数据源
- DataParser: 数据解析器基类
- get_parser: 获取解析器工厂函数
"""

from .iface import (
    CacheStorage,
    MediaStorage,
    PartitionableStorage,
    PartitionOrBatchProgress,
    ProgressInfo,
    StorageABC,
    # DataSource
    DataSource,
    DataSourceInfo,
)
from .iface import PartitionableStorage as DataFlowStorage
from .file_storage import FileStorage, FileMediaStorage, FileCacheStorage
from .s3_storage import S3Storage, S3MediaStorage, S3CacheStorage
from .data_parser import (
    DataParser,
    JsonParser,
    JsonlParser,
    CsvParser,
    ParquetParser,
    PickleParser,
    get_parser,
)
from .datasources import (
    FileDataSource,
    S3DataSource,
    HuggingFaceDataSource,
    ModelScopeDataSource,
    create_data_source,
)

__all__ = [
    # 接口
    "StorageABC",
    "PartitionableStorage",
    "DataFlowStorage",
    "MediaStorage",
    "CacheStorage",
    "PartitionOrBatchProgress",
    "ProgressInfo",
    # DataSource
    "DataSource",
    "DataSourceInfo",
    "FileDataSource",
    "S3DataSource",
    "HuggingFaceDataSource",
    "ModelScopeDataSource",
    "create_data_source",
    # 实现
    "FileStorage",
    "S3Storage",
    "FileMediaStorage",
    "S3MediaStorage",
    "FileCacheStorage",
    "S3CacheStorage",
    # 数据解析器
    "DataParser",
    "JsonParser",
    "JsonlParser",
    "CsvParser",
    "ParquetParser",
    "PickleParser",
    "get_parser",
]
