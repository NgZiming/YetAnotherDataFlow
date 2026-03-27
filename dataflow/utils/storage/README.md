# Storage 模块

数据面/控制面分离的存储接口，支持 DataSource 读取和分片处理。

## 核心设计

### 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                        Pipeline                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ DataSource   │───▶│  Storage     │───▶│  Operators   │  │
│  │ (只读)       │    │ (只写)       │    │              │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 职责 | 位置 |
|---|---|---|
| **DataSource** | 从各种数据源读取数据（只读） | `iface.py` / `datasources.py` |
| **Storage** | 存储和分片数据（只写） | `file_storage.py` / `s3_storage.py` |
| **DataParser** | 文件格式解析（bytes ↔ DataFrame） | `data_parser.py` |

## DataSource - 数据源抽象

### 接口定义

```python
class DataSource(ABC):
    @abstractmethod
    def get_info(self) -> DataSourceInfo:
        """获取数据源信息"""
        pass
    
    @abstractmethod
    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        """流式读取数据，逐行 yield dict"""
        pass
```

### 实现类

| 类 | 描述 | 使用场景 |
|---|---|---|
| **FileDataSource** | 本地文件数据源 | 本地 JSONL/CSV/Parquet 文件 |
| **S3DataSource** | S3 对象存储数据源 | S3 存储的数据 |
| **HuggingFaceDataSource** | HuggingFace datasets | HF 数据集 |
| **ModelScopeDataSource** | ModelScope 数据集 | MS 数据集 |

### 使用示例

```python
from dataflow.utils.storage import create_data_source, FileDataSource, S3DataSource

# 方式 1: 使用工厂函数（自动检测类型）
source = create_data_source("s3://bucket/data.jsonl")
source = create_data_source("/local/path/data.jsonl")

# 方式 2: 显式指定
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/data/"],
    format_type="jsonl"
)

source = FileDataSource(
    paths=["/path/to/data.jsonl"],
    format_type="jsonl"
)

# 读取数据
for row in source.read():
    print(row)  # dict
```

## Storage - 存储接口

### 接口定义

```python
class StorageABC:
    def read(self, output_type: str = "dataframe") -> Any:
        """读取当前上下文数据（必须已调用 load_partition）"""
        pass
    
    def get_keys(self) -> list[str]:
        """获取字段名列表"""
        pass

class PartitionableStorage(StorageABC):
    def split_input(self, num_partitions: int) -> list[str]:
        """分片输入数据（必须首先调用）"""
        pass
    
    def load_partition(self, dependent_steps: list[int]) -> pd.DataFrame:
        """加载依赖步骤的数据并合并"""
        pass
```

### 实现类

| 类 | 描述 | 使用场景 |
|---|---|---|
| **FileStorage** | 本地文件存储 | 本地缓存和输出 |
| **S3Storage** | S3 对象存储 | S3 缓存和输出 |

### 使用流程

```python
from dataflow.utils.storage import (
    S3DataSource,
    FileStorage,
)

# 1. 创建 DataSource
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/input/"],
    format_type="jsonl"
)

# 2. 创建 Storage（data_source 必选）
storage = FileStorage(
    data_source=source,
    cache_path="./cache",
    cache_type="jsonl"
)

# 3. 分片输入数据（必须首先调用，即使只分一片）
storage.split_input(num_partitions=10)
# 此时 storage.files = partition_paths，batch_step 可以遍历 0~9

# 4. 处理步骤 0（不需要 load_partition）
storage.batch_step = 0
storage.operator_step = 0
df = storage.read()  # 直接读 files[0]
result = process(df)
storage.write(result)

# 5. 处理步骤 >0（必须先 load_partition）
storage.operator_step = 1
storage.batch_step = 0
storage.load_partition(dependent_steps=[0])  # 加载步骤 0 的数据
df = storage.read()  # 返回 load_partition 的结果
result = process(df)
storage.write(result)
```

## DataParser - 数据解析器

### 接口定义

```python
class DataParser(ABC):
    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """流式解析，逐行 yield dict"""
        pass
    
    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """序列化到文件"""
        pass
```

### 实现类

| 类 | 格式 | 解析方式 |
|---|---|---|
| **JsonParser** | JSON | `pd.read_json(chunksize=...)` |
| **JsonlParser** | JSONL | `pd.read_json(lines=True, chunksize=...)` |
| **CsvParser** | CSV | `pd.read_csv(chunksize=...)` |
| **ParquetParser** | Parquet | `pd.read_parquet(chunksize=...)` |
| **PickleParser** | Pickle | `pd.read_pickle()` (不支持 chunksize) |

### 使用示例

```python
from dataflow.utils.storage import get_parser

parser = get_parser("jsonl")

# 解析
with open("data.jsonl", "rb") as f:
    for row in parser.parse_to_dataframe(f):
        print(row)  # dict

# 序列化
import pandas as pd
df = pd.DataFrame([{"a": 1, "b": 2}])
parser.serialize_to_file(df, "output.jsonl")
```

## 关键设计原则

### 1. DataSource 与 Storage 分离

- **DataSource**：只负责读取，不关心存储
- **Storage**：只负责写入，从 DataSource 获取输入

```
DataSource (读) ───▶ Storage (写) ───▶ Operators (处理)
```

### 2. 强制分片流程

所有数据必须先调用 `split_input()` 才能读取：

```python
storage.split_input(num_partitions=1)  # 可以只分一片
storage.read()  # ✅ 可以读取
```

### 3. 流式处理

所有解析器返回 `Generator[dict, None, None]`，逐行 yield，避免内存爆炸：

```python
for row in parser.parse_to_dataframe(f, chunk_size=1000):
    process(row)  # 一次处理一行
```

### 4. load_partition 合并依赖

多步骤处理时，`load_partition()` 负责合并依赖步骤的数据：

```python
storage.load_partition(dependent_steps=[0, 1])  # 合并步骤 0 和 1
df = storage.read()  # 返回合并后的数据
```

## 文件结构

```
storage/
├── __init__.py          # 模块导出
├── iface.py             # 接口定义（DataSource, StorageABC, PartitionableStorage）
├── datasources.py       # DataSource 实现（FileDataSource, S3DataSource, ...）
├── data_parser.py       # DataParser 实现（JsonParser, JsonlParser, ...）
├── file_storage.py      # FileStorage 实现
├── s3_storage.py        # S3Storage 实现
└── s3_plugin.py         # S3 工具函数
```
