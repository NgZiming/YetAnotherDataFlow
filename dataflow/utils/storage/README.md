# Storage 模块

数据面/控制面分离的存储接口设计。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                        Storage 模块                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐   │
│  │  DataParser (数据解析器)                             │   │
│  │  ┌───────────────────────────────────────────────┐  │   │
│  │  │  parse_to_dataframe(bytes) -> DataFrame       │  │   │
│  │  │  serialize_from_dataframe(DataFrame) -> bytes │  │   │
│  │  └───────────────────────────────────────────────┘  │   │
│  │                                                      │   │
│  │  实现：JsonParser, JsonlParser, CsvParser,          │   │
│  │        ParquetParser, PickleParser                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ▲                                  │
│                          │ 委托 (bytes 转换)                 │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  StorageABC (数据面)                                 │   │
│  │  - read() / write() / get_keys()                    │   │
│  │  使用者：Operator                                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ▲                                  │
│                          │ 继承                              │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  PartitionableStorage (控制面)                       │   │
│  │  - step() / load_partition() / split_input()        │   │
│  │  - batch_size / batch_step / current_chunk          │   │
│  │  - write_file_path() / file_exists()                │   │
│  │  使用者：Pipeline                                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ▲                                  │
│                          │ 实现                              │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  FileStorage  |  S3Storage                           │   │
│  │  职责：与存储系统沟通，负责 bytes 的读写               │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ▲                                  │
│                          │ 扩展接口                          │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  MediaStorage  |  CacheStorage                       │   │
│  │  - 媒体文件读取  |  - 进度信息持久化                   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 设计原则

### 1. 数据面 / 控制面分离

| 层面       | 使用者   | 职责                                 |
| ---------- | -------- | ------------------------------------ |
| **数据面** | Operator | 只关心数据读写，不关心分片/批次/并发 |
| **控制面** | Pipeline | 管理分片调度、批次处理、断点续传     |

### 2. 存储系统 / 文件格式分离

| 模块           | 职责                                             |
| -------------- | ------------------------------------------------ |
| **Storage**    | 与存储系统沟通（本地文件/S3），负责 bytes 的读写 |
| **DataParser** | 文件格式解析，负责 bytes ↔ DataFrame 转换        |

### 3. 日志规范

所有类在 `__init__` 中初始化 `self.logger = get_logger()`，内部日志统一使用 `self.logger.xxx`：

```python
class MyStorage:
    def __init__(self):
        self.logger = get_logger()

    def do_something(self):
        self.logger.info("Doing something...")
        self.logger.debug("Debug details")
        self.logger.warning("Warning message")
        self.logger.error("Error message")
```

**例外**：`data_parser.py` 使用模块级 logger（`logger = get_logger()`），因为解析器是无状态的工厂对象。

### 4. 核心设计概念

| 概念                | 说明                                               |
| ------------------- | -------------------------------------------------- |
| **files**           | 输入文件列表，`files[batch_step]` 是当前分片的输入 |
| **operator_step**   | 算子步骤索引，决定输出到第几步                     |
| **batch_step**      | 批次/分片索引，决定处理哪个分片                    |
| **id_key**          | 用于合并多步骤数据的唯一键字段名                   |
| **_is_partitioned** | 标记是否进行过分片，用于区分读取逻辑               |
| **_current_chunk**  | 预加载的数据块，用于优化性能                       |

## 文件路径规则

```
operator_step == 0: files[batch_step]                    # 输入文件
operator_step > 0:  output_path/step_{N:08}/part_{M:08}  # 输出文件
```

### 示例

```
# 输入
files = ["input.jsonl"]
batch_step = 0
→ files[0] = "input.jsonl"

# 输出 (operator_step = 1, batch_step = 3)
FileStorage: "cache/step_00000002/part_00000004.jsonl"
S3Storage:   "s3://bucket/output/step_00000002/part_00000004.jsonl"

# 分片 (split_input 后)
files = ["cache/step_00000000/00000001.jsonl", ...]
batch_step = 0
→ files[0] = "cache/step_00000000/00000001.jsonl"
```

## 接口定义

### StorageABC (数据面)

```python
class StorageABC(ABC):
    """基础存储接口 - 数据面。"""

    @abstractmethod
    def read(self, output_type: Literal["dataframe", "dict"] = "dataframe") -> Any:
        """读取当前上下文的数据。"""
        pass

    @abstractmethod
    def write(self, data: Any) -> str:
        """写入数据到当前上下文。"""
        pass

    @abstractmethod
    def get_keys(self) -> list[str]:
        """获取当前数据的字段名列表。"""
        pass
```

### PartitionableStorage (控制面)

```python
class PartitionableStorage(StorageABC):
    """支持分片/批处理的存储接口 - 控制面扩展。"""

    # 属性
    @property
    def batch_size(self) -> Optional[int]: ...

    @property
    def batch_step(self) -> int: ...

    @property
    def current_chunk(self) -> Optional[pd.DataFrame]: ...

    # 方法
    def step(self) -> 'PartitionableStorage': ...
    def load_partition(dependent_steps: list[int]) -> pd.DataFrame: ...
    def split_input(num_partitions: int) -> list[str]: ...
    def write_file_path() -> str: ...
    def file_exists(file_path: str) -> bool: ...
```

### DataParser

```python
class DataParser(ABC):
    """数据解析器抽象基类。"""

    @abstractmethod
    def parse_to_dataframe(self, data: bytes) -> pd.DataFrame:
        """将 bytes 解析为 DataFrame。"""
        pass

    @abstractmethod
    def serialize_from_dataframe(self, df: pd.DataFrame) -> bytes:
        """将 DataFrame 序列化为 bytes。"""
        pass
```

### MediaStorage

```python
class MediaStorage(ABC):
    """媒体存储抽象基类。"""

    @abstractmethod
    def read_media_bytes(self, media_path: str) -> bytes:
        """读取媒体文件的字节数据。"""
        pass
```

### CacheStorage

```python
class CacheStorage(ABC):
    """进度存储的抽象基类。"""

    @abstractmethod
    def record_progress(self, progress: ProgressInfo):
        """记录处理进度。"""
        pass

    @abstractmethod
    def get_progress(self) -> ProgressInfo:
        """获取存储的处理进度。"""
        pass
```

## 使用示例

### FileStorage 初始化

```python
from dataflow.utils.storage import FileStorage

# 单文件输入
storage = FileStorage(
    first_entry_file_name="input.jsonl",
    id_key="id",
    cache_path="./cache",
    cache_type="jsonl",
)

# 多文件输入（未分片）
storage.files = ["a.jsonl", "b.jsonl", "c.jsonl"]
# read() 会自动合并所有文件
```

### S3Storage 初始化

```python
from dataflow.utils.storage import S3Storage

storage = S3Storage(
    endpoint="https://s3.amazonaws.com",
    ak="your-access-key",
    sk="your-secret-key",
    s3_paths=["s3://bucket/input.jsonl"],  # 支持目录
    output_s3_path="s3://bucket/output",
    id_key="id",
    cache_type="jsonl",
)
```

### Operator 使用（数据面）

```python
class MyOperator:
    def run(self, storage: StorageABC):
        # 直接读，不关心分片
        df = storage.read()
        
        # 处理数据
        result = self.process(df)
        
        # 直接写
        storage.write(result)
```

### Pipeline 使用（控制面）

```python
# 1. 初始化
storage = FileStorage("input.jsonl")

# 2. 分割输入（如果需要分片处理）
partition_paths = storage.split_input(num_partitions=10)
# storage.files 已更新为分片路径列表

# 3. 处理每个分片
for i in range(10):
    storage.batch_step = i

    # 读取当前分片
    df = storage.read()

    # 处理
    result = op.run(df)

    # 写入
    storage.write(result)

    # 推进到下一步
    storage = storage.step()

# 4. 多步骤数据合并（取交集）
storage.batch_step = i
df = storage.load_partition(dependent_steps=[0, 1])
# 返回 step0 和 step1 的 id_key 交集数据
```

### 断点续传

```python
path = storage.write_file_path()
if storage.file_exists(path):
    print("已处理过，跳过")
else:
    op.run(storage=storage)
```

### 直接调用解析器

```python
from dataflow.utils.storage import get_parser

parser = get_parser("jsonl")
df = parser.parse_to_dataframe(b'{"a":1}\n{"a":2}\n')
bytes_data = parser.serialize_from_dataframe(df)
```

### 进度跟踪

```python
from dataflow.utils.storage import FileCacheStorage, ProgressInfo

cache = FileCacheStorage(cache_file="./progress.json")

# 记录进度
progress: ProgressInfo = {
    "shard_type": "partition",
    "partitions": [
        {"id": 0, "completed_steps": [0, 1], "current_steps": [2], "steps_rows_nums": {0: 100, 1: 80}}
    ],
    "total_shards": 10,
    "total_steps": 5,
    "overall_status": "running",
}
cache.record_progress(progress)

# 读取进度
saved_progress = cache.get_progress()
```

## 文件结构

```
storage/
├── __init__.py       # 统一导出
├── iface.py          # StorageABC + PartitionableStorage 接口 + 类型定义
├── data_parser.py    # 数据解析器（bytes ↔ DataFrame）
├── file_storage.py   # FileStorage + FileMediaStorage + FileCacheStorage 实现
├── s3_storage.py     # S3Storage + S3MediaStorage + S3CacheStorage 实现
└── README.md         # 本文档
```

## 支持的格式

| 格式    | 解析器        | 扩展名     | 特性                             |
| ------- | ------------- | ---------- | -------------------------------- |
| JSON    | JsonParser    | `.json`    | 标准 JSON 数组格式               |
| JSONL   | JsonlParser   | `.jsonl`   | 每行一个 JSON 对象，适合流式处理 |
| CSV     | CsvParser     | `.csv`     | 逗号分隔，index=False            |
| Parquet | ParquetParser | `.parquet` | 列式存储，支持压缩               |
| Pickle  | PickleParser  | `.pkl`     | Python 原生格式，⚠️ 仅信任数据源  |

## 扩展新格式

1. 在 `data_parser.py` 中添加新的 Parser 类：

```python
class MyFormatParser(DataParser):
    def __init__(self):
        self.logger = get_logger()

    def parse_to_dataframe(self, data: bytes) -> pd.DataFrame:
        try:
            # 解析逻辑
            pass
        except Exception as e:
            self.logger.error(f"Failed to parse MyFormat: {e}")
            raise ValueError(f"MyFormat parsing failed: {e}")

    def serialize_from_dataframe(self, df: pd.DataFrame) -> bytes:
        try:
            # 序列化逻辑
            pass
        except Exception as e:
            self.logger.error(f"Failed to serialize DataFrame to MyFormat: {e}")
            raise ValueError(f"MyFormat serialization failed: {e}")
```

2. 注册到 `PARSER_REGISTRY`：

```python
PARSER_REGISTRY["myformat"] = MyFormatParser
```

## load_partition 详解

`load_partition` 用于多步骤并行处理后的数据合并：

```
步骤 0 输出：{id1, id2, id3, id4, id5}
步骤 1 输出：{id2, id3, id4, id5, id6}
步骤 2 输出：{id3, id4, id5, id6, id7}
----------------------------------------
交集结果：  {id3, id4, id5}  ← 只保留所有步骤都有的数据
```

**应用场景：**
- 多步骤并行过滤，需要取交集
- 多步骤并行转换，需要合并所有步骤的结果
- 确保数据一致性，只处理所有步骤都成功的数据

**实现逻辑：**
1. 遍历 `dependent_steps` 中的每个步骤
2. 从每个步骤加载数据，按 `id_key` 去重合并
3. 取所有步骤的 `id_key` 交集
4. 返回交集数据

## read() 读取逻辑

```python
def read(self, output_type: Literal["dataframe", "dict"]) -> Any:
    if self._current_chunk is not None:
        # 1. 预加载的数据块优先返回
        df = self._current_chunk
    elif self.operator_step == 0 and not self._is_partitioned:
        # 2. 未分片：读取所有 files 并合并
        self.logger.info(f"Reading all input files: {self.files}")
        df = pd.DataFrame(list(self._read_all()))
    else:
        # 3. 已分片或后续步骤：读取单个文件
        file_path = self._get_cache_file_path(self.operator_step)
        self.logger.info(f"Reading file: {file_path}")
        df = self._parser.parse_to_dataframe(...)
    return df
```

| 场景     | 条件                                         | 读取行为                 |
| -------- | -------------------------------------------- | ------------------------ |
| 预加载   | `_current_chunk is not None`                 | 直接返回                 |
| 未分片   | `operator_step == 0 and not _is_partitioned` | 读取所有 files 合并      |
| 已分片   | `operator_step == 0 and _is_partitioned`     | 读取 `files[batch_step]` |
| 后续步骤 | `operator_step > 0`                          | 读取 `step_{N}/part_{M}` |

## 日志级别使用指南

| 级别    | 使用场景                                 | 示例                                |
| ------- | ---------------------------------------- | ----------------------------------- |
| info    | 关键操作（文件读写、分片创建、处理开始） | "Reading file: xxx", "Wrote N rows" |
| debug   | 详细调试信息（通常开发时使用）           | "Using parser for format: jsonl"    |
| warning | 非致命错误（文件已存在、跳过处理）       | "Partition already exists"          |
| error   | 解析/序列化失败、不支持的操作            | "Failed to parse JSON: ..."         |

## 依赖关系

```
dataflow.utils.storage
├── dataflow.logger (get_logger)
├── boto3 (S3Storage 需要)
├── botocore (S3 配置)
├── pandas (数据处理)
└── tqdm (进度条)
```

## S3 路径解析 (s3_plugin.py)

```python
from dataflow.utils.s3_plugin import (
    split_s3_path,
    list_s3_objects_detailed,
    get_s3_client,
    exists_s3_object,
    is_s3_object_empty,
    read_s3_bytes,
    put_s3_object,
)

# 解析路径
bucket, key = split_s3_path("s3://my-bucket/path/to/file.jsonl")
# bucket = "my-bucket", key = "path/to/file.jsonl"

# 创建客户端
client = get_s3_client(endpoint, ak, sk)

# 检查存在
exists = exists_s3_object(client, "s3://bucket/file.jsonl")

# 检查是否为空
is_empty = is_s3_object_empty(client, "s3://bucket/file.jsonl")

# 读取内容
data = read_s3_bytes(client, "s3://bucket/file.jsonl")

# 写入内容
put_s3_object(client, "s3://bucket/file.jsonl", b"content")

# 列出对象
for s3_path, metadata in list_s3_objects_detailed(client, "s3://bucket/prefix/", recursive=True):
    print(s3_path, metadata)
```
