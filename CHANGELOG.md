# DataFlow Changelog

## 版本对比：v1.0.0 (Workspace) vs v1.0.10 (Release)

> 本 Changelog 对比当前工作区版本（v1.0.0）与发布版本（v1.0.10）的主要差异

---

## 📊 总体统计

| 项目 | v1.0.10 | v1.0.0 (Workspace) | 变化 |
|------|---------|-------------------|------|
| Python 文件数 | 399 | 407 | +8 |
| Storage 模块文件 | 1 (storage.py) | 6 (重构) | 架构重构 |
| Storage 模块代码行数 | 1184 | 2261 | +91% |
| 最近提交 | 2026-03-27 | 2026-03-30 | +3 天 |

---

## 🚀 重大更新

### 1. Storage 模块架构重构

**v1.0.10**: 单一文件 `storage.py` (1184 行)

**v1.0.0**: 重构为 6 个模块化文件 (2261 行)

```
dataflow/utils/storage/
├── __init__.py      # 模块导出接口
├── iface.py         # 接口定义 (StorageABC, PartitionableStorage, DataSource)
├── datasources.py   # 数据源实现 (FileDataSource, S3DataSource, HuggingFaceDataSource, ModelScopeDataSource)
├── file_storage.py  # 本地存储实现 (FileStorage, FileCacheStorage, FileMediaStorage)
├── s3_storage.py    # S3 存储实现 (S3Storage, S3CacheStorage, S3MediaStorage)
└── data_parser.py   # 数据解析器 (JsonParser, CsvParser, ParquetParser, etc.)
```

**设计改进**:
- ✅ **数据面/控制面分离**: `StorageABC` (数据读写) vs `PartitionableStorage` (分片管理)
- ✅ **DataSource 抽象**: 统一的数据源接口，支持本地文件、S3、HuggingFace、ModelScope
- ✅ **DataParser 独立**: 文件格式解析逻辑独立，支持扩展新格式

---

### 2. 智能行数估算 (`estimate_total_rows`)

**新增功能**: 无需读取全部数据即可估算总行数

| 格式 | v1.0.10 | v1.0.8 |
|------|---------|--------|
| Parquet | ❌ 不支持 | ✅ O(1) 读取 metadata |
| CSV/JSONL | ❌ 不支持 | ✅ 采样估算 |
| S3 Parquet | ❌ 不支持 | ✅ 仅下载 footer (~64KB) |
| HuggingFace | ❌ 不支持 | ✅ API 直接获取 |

**使用示例**:
```python
# Parquet - O(1) 复杂度
source = FileDataSource(paths=["data/large.parquet"], format_type="parquet")
total_rows = source.estimate_total_rows()  # 直接读取 metadata

# S3 Parquet - 仅下载 64KB footer
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/large.parquet"],
    format_type="parquet"
)
total_rows = source.estimate_total_rows()  # 仅 64KB 网络传输
```

---

### 3. Pipeline 接口简化

**v1.0.10**: 需要手动管理 `batch_step` 和 `operator_step`

```python
# 旧用法
self.storage.batch_step = 0
self.storage.operator_step = 0
df = self.storage.read()
result = self.operator.run(df, ...)
self.storage.write(result)
```

**v1.0.8**: 使用 `storage.step()` 自动管理

```python
# 新用法
self.operator.run(
    self.storage.step(),  # 自动管理分片和步骤
    input_key="raw_content",
    output_key="translation",
)
```

**改进**:
- ✅ 自动分片：`compile()` 中自动调用 `split_input()`
- ✅ 自动依赖管理：后续步骤自动读取前一步结果
- ✅ 代码简化：无需手动管理 step 编号

---

### 4. PartitionPipelineParallelRun 并行处理

**新增类**: `PartitionPipelineParallelRun` - 支持大规模数据并行处理

**使用示例**:
```python
class LargeFilePipeline(PartitionPipelineParallelRun):
    def __init__(self, partitions: int):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage, partitions)  # 传入分片数量
        
        self.data_source = S3DataSource(...)
        self.storage = S3Storage(...)
    
    def forward(self):
        pass  # 空实现即可


# 使用
pipeline = LargeFilePipeline(1000)  # 1000 个分片
pipeline.compile()
pipeline.forward(max_parallelism=10)  # 10 个并发
```

---

### 5. 数据格式支持扩展

**新增格式**:
- ✅ Parquet (通过 pyarrow)
- ✅ Pickle
- ✅ JSON

**解析器架构**:
```python
# 统一解析接口
parser = get_parser("parquet")  # 或 "jsonl", "csv", "json", "pickle"
df = parser.parse_to_dataframe(file_handle)
```

---

### 6. S3 性能优化

**v1.0.10**: 无特殊优化

**v1.0.0**: 
- ✅ S3 Range 请求：Parquet 文件仅下载 footer (~64KB)
- ✅ 避免下载整个大文件
- ✅ 支持 S3 进度存储 (`S3CacheStorage`)

**性能对比**:
| 文件大小 | v1.0.10 | v1.0.8 | 提升 |
|---------|---------|--------|------|
| 100MB Parquet | 100MB 下载 | ~64KB | 1500x |
| 1GB Parquet | 1GB 下载 | ~64KB | 15000x |
| 10GB Parquet | 10GB 下载 | ~64KB | 150000x |

---

## 📝 详细提交历史

### 2026-03-30

#### `dd465d6` feat: readme
- 更新 README.md 和 README-zh.md
- 优化文档结构和内容

#### `25ddfe3` feat: 优化数据分片和行数估算
- 新增 `estimate_total_rows()` 方法
- Parquet 格式 O(1) 复杂度读取 metadata
- S3 Parquet 仅下载 footer (~64KB)
- 修复 `idx` 增量 bug
- 修复 `partition_path` 拼写错误

#### `a52563b` Storage 接口重构
- 移除 `current_chunk` setter，简化设计
- 数据面/控制面分离
- 新增 DataSource 抽象类
- 新增 DataParser 解析器架构

---

### 2026-03-28

#### `2192ff8` ~ `f544099` data_parser 修复
- 修复 CSV/JSONL 采样解析问题
- 优化 PyArrow Parquet 读取

---

## 🔧 API 变更

### 破坏性变更

#### 1. Storage 初始化

**v1.0.10**:
```python
storage = LazyFileStorage(
    first_entry_file_name="input.jsonl",
    cache_path="./cache",
    cache_type="jsonl"
)
```

**v1.0.0**:
```python
# 需要分别创建 DataSource 和 Storage
data_source = FileDataSource(
    paths=["input.jsonl"],
    format_type="jsonl"
)

storage = FileStorage(
    data_source=data_source,
    id_key="id",  # 新增：必须指定
    cache_path="./cache",
    cache_type="jsonl"
)
```

#### 2. Pipeline 继承

**v1.0.10**:
```python
class MyPipeline(PipelineABC):
    def __init__(self):
        super().__init__(cache_storage)
```

**v1.0.0**:
```python
# 普通 Pipeline
class MyPipeline(PipelineABC):
    def __init__(self):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

# 并行 Pipeline
class LargePipeline(PartitionPipelineParallelRun):
    def __init__(self, partitions: int):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage, partitions)
```

---

## 📚 新增文档

### 新手教程
- `TUTORIAL.md` (中文) - 445 行
- `TUTORIAL-en.md` (英文) - 445 行

**内容包括**:
1. 环境准备
2. 第一个 Pipeline：文本翻译
3. 多步骤 Pipeline
4. S3 数据源支持
5. 常见问题 (FAQ)

---

## 🐛 Bug 修复

| 提交 | 修复内容 |
|------|---------|
| `2192ff8` | data_parser CSV/JSONL采样行读取 |
| `4974548` | data_parser 边界条件处理 |
| `f2ea7c7` | data_parser 采样逻辑优化 |
| `f544099` | PyArrow Parquet 读取兼容性 |

---

## 🔄 迁移指南

### 从 v1.0.10 迁移到 v1.0.8

#### 1. 更新 Storage 初始化

```python
# 旧代码 (v1.0.10)
from dataflow.utils.storage import LazyFileStorage

storage = LazyFileStorage(
    first_entry_file_name="input.jsonl",
    cache_path="./cache",
    cache_type="jsonl"
)

# 新代码 (v1.0.0)
from dataflow.utils.storage import FileDataSource, FileStorage, FileCacheStorage

data_source = FileDataSource(paths=["input.jsonl"], format_type="jsonl")
storage = FileStorage(
    data_source=data_source,
    id_key="id",  # 新增
    cache_path="./cache",
    cache_type="jsonl"
)
```

#### 2. 更新 Pipeline forward 方法

```python
# 旧代码 (v1.0.10)
def forward(self):
    self.storage.batch_step = 0
    self.storage.operator_step = 0
    df = self.storage.read()
    result = self.operator.run(df, input_key="text")
    self.storage.write(result)

# 新代码 (v1.0.0)
def forward(self):
    self.operator.run(
        self.storage.step(),
        input_key="text",
        output_key="result",
    )
```

#### 3. 使用新行数估算功能

```python
# 新增功能
total_rows = data_source.estimate_total_rows()
print(f"预估总行数：{total_rows}")
```

---

## 📅 版本信息

| 版本 | 日期 | 状态 |
|------|------|------|
| v1.0.10 | 2026-03-27 | 发布版 |
| v1.0.0 (Workspace) | 2026-03-31 | 开发版 |

---

## 🆕 2026-03-31 更新日志

### 对比基线：`6948eb5` (2026-03-30)

#### 🚀 新功能

##### `b0c12e8` - IdSynthesizer 抽象类
- 新增 `IdSynthesizer` 抽象基类，支持缺失 `id_key` 的自动合成
- 实现 `UuidIdSynthesizer`（默认）和 `CounterIdSynthesizer`
- `FileStorage` 和 `S3Storage` 在 `split_input` 时自动合成缺失的 `id_key`

```python
from dataflow.utils.storage import FileStorage
from dataflow.utils.iface import UuidIdSynthesizer, CounterIdSynthesizer

# 默认自动用 UUID 合成
storage = FileStorage(data_source=source, id_key="id")

# 或者自定义
storage = FileStorage(
    data_source=source,
    id_key="id",
    id_synthesizer=CounterIdSynthesizer(prefix="item", start=1000)
)
```

#### 🚀 OpenClaw CLI Serving 重构

##### `3e9f94b` - 改用预先创建的 worker agents
- 删除临时 agent 的创建和清理逻辑（`_copy_agent`, `delete_agent`）
- 新增 `_setup_worker_agents()` 预先创建 worker agents
- 每次请求前执行 `/new` 创建新 session
- 请求轮询分配给不同的 worker agent

##### `fc108ba` - Pipeline 并行检查优化
- `_check_completed_workloads` 改为多线程并行检查
- 预先拷贝 storage 并设置 `batch_step`，避免多线程竞争
- 使用 `ThreadPoolExecutor`，最多 32 个 worker 并行检查

##### `cce4704` - Session 文件等待增强
- `load_session` 改为抛异常，不再返回 `None`
- `_resolve_transcript_path` 超时从 15 秒延长到 60 秒
- 超时后抛出 `FileNotFoundError`

#### 🐛 Bug 修复

| 提交 | 修复内容 |
|------|---------|
| `79c18b0` | 添加 agent 注册重试逻辑，解决竞态条件 |
| `fdfc9d1` | 修复 openclaw CLI 命令，使用 `agents delete` 和 `-m` 标志 |
| `7601512` | CLI serving 改用临时 agent 而非 `--session-id` |
| `a29d612` | 修复 data_parser 和 datasources 读取问题 |
| `9865233` | 修复文件未找到问题 |

#### 📝 其他更新

| 提交 | 内容 |
|------|------|
| `c734aa6` | shelex 功能更新 |
| `c02c256` | 修复 agent 目录 |
| `0023d74` | 修复 agent 目录 |
| `53561ff` | 保存所有 sessions |
| `c731aa0` | API 功能更新 |
| `9c3781a` | 支持嵌套 keys |
| `a733690` | 默认模型配置 |
| `d5d806b` | 格式化 |

**注意**: Workspace 版本号为 1.0.0，但功能上比 v1.0.10 更新。建议将 Workspace 版本升级为 1.1.0。

---

## 👥 贡献者

- 主要开发者：wuziming
- 设计贡献：zmwu (Storage 接口重构)

---
