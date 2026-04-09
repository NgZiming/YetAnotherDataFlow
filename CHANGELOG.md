# DataFlow Changelog

All notable changes to DataFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.6.post1] - 2026-04-09

### Fixed

- **Session lock 文件路径修复** (`c70136f`, `909c5fe`)
  - 修正 session lock 文件检测位置（lock 文件实际位于 `agent/sessions/` 目录下，而非 workspace 根目录）
  - 修复 `_is_agent_locked()` 使用正确的 `sessions/` 子目录路径
  - 修复 `/new` 失败时的 lock 文件清理逻辑
  - 将未定义的 `_cleanup_agent_locks` 函数调用替换为内联逻辑

- **Thread safety** (`f7b4a01`)
  - 修复 S3Storage 的线程安全问题

---

## [1.0.6] - 2026-04-08

### Added

- **NanobotServing** (`5fe27e4`)
  - 新增 `NanobotServing` - 基于 nanobot Python SDK 的轻量级 Serving 类
  - 新增 `CLINanobotServing` 测试及 API 配置支持

- **CLI 请求增强** (`4502473`)
  - 添加 CLI 请求重试机制
  - 添加 tqdm 进度显示

- **缓存配置优化** (`e696b26`, `524e002`)
  - `S3DataSource` 和 `S3Storage` 的 `cache_max_size_gb` 改为可配置参数

### Changed

- **缓存架构重构** (`ac516d2`)
  - 重构 `LRUCacheManager` 缓存架构
  - 将缓存逻辑从 `DataParser` 层移至 `Storage/DataSource` 层

- **命名统一** (`55a1b50`, `a8d4fe4`)
  - `S3Storage` 的 `temp_dir` 参数改为 `cache_dir`，统一命名
  - `nanobot_serving` 重命名为 `cli_nanobot_serving` 以对齐命名规范
  - 重构 `Serving` 类命名并添加 agent 健康检查

### Fixed

- **缓存大小配置** (`722f1a5`, `66b96e4`)
  - 修复 cache size 配置问题
  - 修复类型注解问题

- **依赖冲突** (`ccfb7af`)
  - 解决 langkit 依赖冲突问题

- **临时目录支持** (`40ca1ae`)
  - 为 `DataParser` 添加自定义临时目录支持

---

## [1.0.5] - 2026-04-07

### Fixed

- **Unicode surrogate 字符清理** (`data_parser.py`)
  - 新增 `clean_surrogates()` 工具函数，递归清理字符串中的 U+D800-U+DFFF 字符
  - 在 `DataParser` 基类中添加 `_clean_data_for_serialization()` 静态方法
  - 所有 Parser (Json/Jsonl/Csv/Parquet/Pickle) 的 `serialize_to_file()` 都会先清理数据
  - 修复 `'utf-8 codec can't encode character surrogates not allowed'` 错误

---

## [1.0.4.post1] - 2026-04-03

### Fixed

- **Pipeline.is_partitioned 同步问题** (`main`)  
  - `Pipeline.compile()`: 在设置 `self.storage.is_partitioned = True` 后，同步更新所有 operator nodes 中的 storage
  - 修复 `_build_operator_nodes_graph()` 在 `is_partitioned` 设置之前被调用导致的问题
  - 确保 `execute_workload()` 中 `copy.copy(node.storage)` 获得正确的 `is_partitioned` 值

- **entrypoint.sh heredoc 语法** (`main`)  
  - 修复 `openclaw config set` 不支持直接 heredoc 的问题
  - 改为使用 `$(cat << 'EOF')` 命令替换
  - 移除环境变量默认值，强制要求设置
  - 修改 gateway 启动方式为 `nohup openclaw gateway run > gateway.log 2>&1 &`

---

## [1.0.4] - 2026-04-03

### Added

- **Pipeline 分片跳过优化** (`b13860c`)
  - `Pipeline.compile()`: 检查 progress 中的 `total_shards`，已分片则跳过 `split_input()`
  - 新增 `is_partitioned` 属性到 `PartitionableStorage` 接口
  - 支持任务重启时跳过已完成分片，避免重复处理

- **Storage 接口优化** (`b13860c`)
  - 移除 `batch_size` 属性（分片时动态计算）
  - `get_keys()` 从 DataSource 读取字段名

### Changed

- **Docker 镜像优化** (`13bb3bb`)
  - 代码拷贝路径改为 `/opt/dataflow`
  - 更新 `.dockerignore` 排除非运行时文件
    - `dataflow/example/` - 示例数据
    - `dataflow/cli_funcs/` - CLI 功能
    - `dataflow/webui/` - Web UI
    - `static/` - 静态资源

- **本地安装支持** (`85319e6`)
  - Dockerfile 改为安装本地文件夹而非远程 git
  - 移除包含敏感信息的远程 git URL

### Removed

- **BatchedPipeline 相关代码** (`b13860c`)
  - 删除 `BatchedPipelineABC`, `StreamBatchedPipelineABC` 类
  - 删除 `BatchedFileStorage`, `StreamBatchedFileStorage` 类
  - 删除测试文件 `test/test_batched_pipeline.py`, `test/test_batched_stream_pipeline.py`
  - 删除模板文件 `my_pipeline.py`

### Fixed

- **Pipeline 进度初始化** (`b13860c`)
  - `progress["partitions"]` 列表长度改为 `self._partitions`
  - `_build_operator_nodes_graph()` 移到 progress 创建之前

- **Pipeline 类名获取** (`b13860c`)
  - `pipeline_class` 改为 `type(self).__bases__[0].__name__`
  - 确保获取基类名 (`PipelineABC` 或 `PartitionPipelineParallelRun`)

- **依赖修复** (`589a542`)
  - `requirements.txt` 新增依赖

---

## [1.0.3] - 2026-04-02

### Added

- **核心算子模块** (`7fee81e`)
  - `JsonParseFilter`: JSON 解析和验证算子，支持字段类型检查、正则匹配、数值范围验证
  - `NestExtractOperator`: 嵌套 JSON 提取算子，支持点号路径 (`user.address.city`) 和数组索引 (`items[0].name`) 语法
  - `FormatStrPromptedAgenticGenerator`: 基于模板提示的 Agent 生成算子，支持传递文件内容数据
  - `FileContextGenerator`: 文件内容合成算子，根据文件路径和问题生成表格/文档/PPT/代码等内容

- **二进制文件生成系统** (`95b10c0`, `7055f70`)
  - `generate_binary_files.py`: 支持 11 种格式的测试文件生成 (CSV/XLSX/PDF/DOCX/PPTX/JSON/XML/HTML/YAML/TXT/Py/JS/TS)
  - `CLIOpenClawServing` 增强：支持在 LLM 调用前注入二进制文件内容数据
  - 5 类 Prompt 模板：table/document/presentation/structured/text/code

- **依赖更新** (`a095d18`)
  - 新增 `openpyxl` - Excel 文件读写
  - 新增 `python-pptx` - PPTX 文件生成
  - 新增 `reportlab` - PDF 文件生成
  - 新增 `docx` - DOCX 文件生成

### Fixed

- **文件验证和路径处理** (`5186b80`)
  - `generate_file` 内容验证逻辑修复
  - 路径处理从 `/workspace/` 到 agent workspace 的转换

- **Pipeline 输入检查** (`cd0b60a`)
  - Pipeline 步骤输入键检查增强

### Changed

- **版本更新** (`version.py`)
  - 从 v1.0.2 升级到 v1.0.3

---

## [1.0.2] - 2026-04-01

### Fixed

- **Pipeline 输入键检查增强** (`ad3bdf3`, `fc60bf0`)
  - `PipelineABC._check_input_keys`: 添加空 `input_key_first_part` 检查，避免空字符串导致的错误
  - `PipelineABC._check_input_keys_for_step`: 同上，确保后续步骤的输入键检查健壮性
  - `PartitionPipelineParallelRun`: 跳过以 `.` 开头的 `key_para_name`，避免错误的依赖添加

- **OpenClaw CLI Serving 超时处理** (`ab06a92`, `c6d20a9`)
  - `_execute_single_query`: timeout 从硬编码 30 秒改为使用传入的 `timeout` 参数
  - 超时异常从返回空字符串改为抛出异常，确保调用方能正确处理超时
  - 返回值格式统一为 `{"messages": [...]}` 结构

- **Storage schema 包含 id_key** (`bacc6c1`, `814176f`)
  - `FileStorage.get_schema`: 返回的 schema 包含 `id_key`，确保后续步骤能正确识别主键
  - `S3Storage.get_schema`: 同上

- **文件句柄泄漏修复** (`6cd60cd`)
  - `FileStorage._load_data_for_pruning`: 添加 try-finally 确保文件正确关闭

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
