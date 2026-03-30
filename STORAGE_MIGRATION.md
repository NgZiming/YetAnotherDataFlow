# Storage 接口迁移指南

## 概述

`dataflow.utils.storage` 模块的接口发生了重大变化，采用 **DataSource 和 Storage 分离**的设计模式。

## 核心变化

### 旧接口（已废弃）

```yaml
storage:
  type: "S3Storage"
  params:
    endpoint: "${S3_EP:}"
    ak: "${S3_AK:}"
    sk: "${S3_SK:}"
    s3_paths:          # ❌ 直接在 Storage 中配置输入路径
      - s3://bucket/data.jsonl
    output_s3_path: "s3://bucket/output/"
    id_key: "id"
```

**问题：**
- Storage 同时负责读取和写入，职责不清
- 无法灵活切换数据源
- 不支持多种数据源类型（HF, MS 等）

### 新接口（推荐）

```yaml
# 1. 定义数据源
data_sources:
  default:
    type: "S3DataSource"
    params:
      endpoint: "${S3_EP:}"
      ak: "${S3_AK:}"
      sk: "${S3_SK:}"
      s3_paths:
        - s3://bucket/data.jsonl
      format_type: "jsonl"  # 可选：jsonl, json, csv, parquet

# 2. Storage 引用数据源
storage:
  type: "S3Storage"
  data_source: "default"    # ✅ 引用 DataSource 名称
  params:
    output_s3_path: "s3://bucket/output/"
    id_key: "id"
```

**优势：**
- DataSource 只负责读取，Storage 只负责写入
- 支持多种数据源类型
- 可以灵活切换数据源而不修改 Storage 配置

## 支持的组件

### DataSource 类型

| 类型 | 模块 | 描述 |
|---|---|---|
| `S3DataSource` | `dataflow.utils.storage` | S3 对象存储 |
| `FileDataSource` | `dataflow.utils.storage` | 本地文件 |
| `HuggingFaceDataSource` | `dataflow.utils.storage` | HuggingFace 数据集 |
| `ModelScopeDataSource` | `dataflow.utils.storage` | ModelScope 数据集 |

### Storage 类型

| 类型 | 模块 | 描述 |
|---|---|---|
| `S3Storage` | `dataflow.utils.storage` | S3 缓存和输出 |
| `FileStorage` | `dataflow.utils.storage` | 本地缓存和输出 |

### MediaStorage 类型

| 类型 | 模块 | 描述 |
|---|---|---|
| `S3MediaStorage` | `dataflow.utils.storage` | S3 媒体存储 |
| `FileMediaStorage` | `dataflow.utils.storage` | 本地媒体存储 |

### CacheStorage 类型

| 类型 | 模块 | 描述 |
|---|---|---|
| `S3CacheStorage` | `dataflow.utils.storage` | S3 进度存储 |
| `FileCacheStorage` | `dataflow.utils.storage` | 本地进度存储 |

## 迁移步骤

### 1. 修改 YAML 配置

**旧格式：**
```yaml
storage:
  type: "S3Storage"
  params:
    endpoint: "${S3_EP:}"
    ak: "${S3_AK:}"
    sk: "${S3_SK:}"
    s3_paths:
      - s3://bucket/input.jsonl
    output_s3_path: "s3://bucket/output/"
    id_key: "warc_record_id"
```

**新格式：**
```yaml
data_sources:
  default:
    type: "S3DataSource"
    params:
      endpoint: "${S3_EP:}"
      ak: "${S3_AK:}"
      sk: "${S3_SK:}"
      s3_paths:
        - s3://bucket/input.jsonl
      format_type: "jsonl"

storage:
  type: "S3Storage"
  data_source: "default"
  params:
    output_s3_path: "s3://bucket/output/"
    id_key: "warc_record_id"
```

### 2. 更新 config_loader.py

使用新的 `config_loader_new.py` 替换旧的 `config_loader.py`。

主要变化：
- 支持 `data_sources` 配置
- `StorageConfig` 新增 `data_source` 字段
- `ComponentFactory.create_storage()` 需要传入 `data_source` 实例
- `PipelineConfigLoader._create_pipeline()` 自动推断 CacheStorage

### 3. 运行流程不变

```python
from dataflow_extensions.config_loader import PipelineConfigLoader

loader = PipelineConfigLoader("configs/my_pipeline.yaml")
pipeline = loader.build()
pipeline.compile()
pipeline.forward(partitions=100, max_parallelism=4)
```

## 完整示例

### 多个数据源

```yaml
data_sources:
  # 主数据源
  main:
    type: "S3DataSource"
    params:
      s3_paths:
        - s3://bucket/train/
      format_type: "jsonl"

  # 验证数据源
  validation:
    type: "HuggingFaceDataSource"
    params:
      dataset: "openai/summarize_from_feedback"
      split: "validation"

storage:
  type: "S3Storage"
  data_source: "main"  # 使用主数据源
  params:
    output_s3_path: "s3://bucket/output/"
```

### 本地文件数据源

```yaml
data_sources:
  default:
    type: "FileDataSource"
    params:
      paths:
        - /local/path/data.jsonl
      format_type: "jsonl"

storage:
  type: "FileStorage"
  data_source: "default"
  params:
    cache_path: "./cache"
    id_key: "id"
```

## 关键设计原则

### 1. 职责分离

```
DataSource (只读) ───▶ Storage (只写) ───▶ Operators (处理)
```

### 2. 强制分片

所有数据必须先调用 `split_input()` 才能读取：

```python
storage.split_input(num_partitions=10)  # 必须首先调用
df = storage.read()  # ✅ 可以读取
```

### 3. 流式处理

所有解析器返回 `Generator[dict, None, None]`，逐行 yield，避免内存爆炸。

### 4. load_partition 合并依赖

多步骤处理时，`load_partition()` 负责合并依赖步骤的数据：

```python
storage.load_partition(dependent_steps=[0, 1])  # 合并步骤 0 和 1
df = storage.read()  # 返回合并后的数据
```

## 文件位置

- 新 config_loader: `/home/SENSETIME/wuziming/.openclaw/workspace/config_loader_new.py`
- 新 YAML 示例: `/home/SENSETIME/wuziming/.openclaw/workspace/merged-workflow-new.yaml`
- 原 storage 接口文档: `/home/SENSETIME/wuziming/.openclaw/sandboxes/agent-main-f331f052/dataflow/dataflow/utils/storage/README.md`

## 后续工作

1. 将 `config_loader_new.py` 复制到实际使用位置
2. 更新所有现有 YAML 配置文件
3. 测试验证新流程是否正常工作
