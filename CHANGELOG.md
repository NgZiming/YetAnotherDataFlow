# DataFlow Changelog

All notable changes to DataFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.1] - 2026-03-31

### Added

- **IdSynthesizer 抽象类** (`b0c12e8`)
  - 新增 `IdSynthesizer` 抽象基类，支持缺失 `id_key` 的自动合成
  - 实现 `UuidIdSynthesizer`（默认）和 `CounterIdSynthesizer`
  - `FileStorage` 和 `S3Storage` 在 `split_input` 时自动合成缺失的 `id_key`

- **OpenClaw CLI Serving 重构** (`3e9f94b`)
  - 改用预先创建的 worker agents，避免重复创建失败
  - 每次请求前执行 `/new` 创建新 session
  - 请求轮询分配给不同的 worker agent

- **Pipeline 并行检查优化** (`fc108ba`)
  - `_check_completed_workloads` 改为多线程并行检查
  - 最多 32 个 worker 并行检查文件存在性

### Changed

- **Session 文件等待增强** (`cce4704`)
  - `load_session` 改为抛异常，不再返回 `None`
  - `_resolve_transcript_path` 超时从 15 秒延长到 60 秒

- **Worker agent 注册等待** (`6d8b994`)
  - 创建 worker agent 后轮询检查是否注册成功
  - 最多等待 5 秒（10 次 * 0.5 秒）

### Fixed

- `79c18b0` - 添加 agent 注册重试逻辑，解决竞态条件
- `fdfc9d1` - 修复 openclaw CLI 命令，使用 `agents delete` 和 `-m` 标志
- `7601512` - CLI serving 改用临时 agent 而非 `--session-id`
- `a29d612` - 修复 data_parser 和 datasources 读取问题
- `9865233` - 修复文件未找到问题

### Other Updates

| Commit | Description |
|--------|-------------|
| `c734aa6` | shelex 功能更新 |
| `c02c256` | 修复 agent 目录 |
| `0023d74` | 修复 agent 目录 |
| `53561ff` | 保存所有 sessions |
| `c731aa0` | API 功能更新 |
| `9c3781a` | 支持嵌套 keys |
| `a733690` | 默认模型配置 |
| `d5d806b` | 格式化 |

---

## [1.0.0] - 2026-03-30

### Added

- **Storage 模块架构重构**
  - 重构为 6 个模块化文件 (iface.py, datasources.py, file_storage.py, s3_storage.py, data_parser.py)
  - 数据面/控制面分离设计
  - DataSource 抽象类，支持本地文件、S3、HuggingFace、ModelScope

- **智能行数估算** (`estimate_total_rows`)
  - Parquet 格式 O(1) 复杂度读取 metadata
  - S3 Parquet 仅下载 footer (~64KB)
  - CSV/JSONL 采样估算

- **PartitionPipelineParallelRun** - 支持大规模数据并行处理

- **数据格式支持扩展**
  - Parquet (通过 pyarrow)
  - Pickle
  - JSON

- **S3 性能优化**
  - S3 Range 请求：Parquet 文件仅下载 footer (~64KB)
  - 支持 S3 进度存储 (`S3CacheStorage`)

### Changed

- **Pipeline 接口简化**
  - 使用 `storage.step()` 自动管理分片和步骤
  - 自动分片：`compile()` 中自动调用 `split_input()`
  - 自动依赖管理：后续步骤自动读取前一步结果

### Documentation

- 新增 `TUTORIAL.md` (中文) - 445 行
- 新增 `TUTORIAL-en.md` (英文) - 445 行
- 更新 `README.md` 和 `README-zh.md`

---

## [1.0.10] - 2026-03-27

> 发布版本

---

## 版本对比

| 项目 | v1.0.10 | v1.0.1 | 变化 |
|------|---------|--------|------|
| Python 文件数 | 399 | 407 | +8 |
| Storage 模块文件 | 1 (storage.py) | 6 (重构) | 架构重构 |
| Storage 模块代码行数 | 1184 | 2261 | +91% |

---

## 迁移指南

### 从 v1.0.10 迁移到 v1.0.1

#### 1. 更新 Storage 初始化

```python
# 旧代码 (v1.0.10)
from dataflow.utils.storage import LazyFileStorage

storage = LazyFileStorage(
    first_entry_file_name="input.jsonl",
    cache_path="./cache",
    cache_type="jsonl"
)

# 新代码 (v1.0.1)
from dataflow.utils.storage import FileDataSource, FileStorage

data_source = FileDataSource(paths=["input.jsonl"], format_type="jsonl")
storage = FileStorage(
    data_source=data_source,
    id_key="id",
    cache_path="./cache",
    cache_type="jsonl"
)
```

#### 2. 使用 IdSynthesizer（可选）

```python
from dataflow.utils.storage import FileStorage
from dataflow.utils.iface import UuidIdSynthesizer

# 默认自动用 UUID 合成
storage = FileStorage(data_source=source, id_key="id")

# 或者自定义
storage = FileStorage(
    data_source=source,
    id_key="id",
    id_synthesizer=CounterIdSynthesizer(prefix="item", start=1000)
)
```

---

## 贡献者

- 主要开发者：wuziming
- 设计贡献：zmwu (Storage 接口重构)
