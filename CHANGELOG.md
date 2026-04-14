# DataFlow Changelog

All notable changes to DataFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.7] - 2026-04-14

### Added

- **OpenClaw Agent Serving 架构重构** (`39a4196`, `4eeaee8`, `cf0e77b`)
  - 新增 `AgentServingABC` 抽象基类，提供统一 Agent 能力流程
  - 新增 `AgentServingABC.MessageDict` 和 `TrajectoryDict` 类型定义
  - 新增 `CLIOpenClawServing` - 基于 OpenClaw CLI 的 Agent serving 实现
  - 新增 `SDKNanobotServing` - 基于 Nanobot SDK 的 Agent serving 实现
  - 新增 `SystemPromptBuilder` - 动态构建 OpenClaw system prompt
  - 支持动态创建 worker agents（按需创建，不再预先创建池）
  - 支持任务验证循环（verification loop）
  - 支持 system prompt 注入（包含 skills 信息）

- **FileContextGenerator 增强** (`0962a63`, `a46e566`)
  - 添加详细日志输出
  - 支持多种输入格式（string/list）
  - 改进错误处理和失败 row 过滤
  - 添加文件生成队列调试信息

- **FormatStrPromptedAgenticGenerator 增强** (`8ebef33`)
  - 支持 `input_skills_dir` 参数动态加载 skills
  - 支持 `input_skills_key` 传递 skill 路径列表
  - 支持 `verification_prompt_template` 验证提示词
  - 支持 `enable_verification` 自动验证循环
  - 支持 `max_verification_rounds` 最大验证轮数

- **JsonParseFilter 容错增强** (`0384c59`)
  - 添加 `json-repair` 依赖
  - 使用 `repair_json` 替代标准 `json.loads`
  - 增强 JSON 解析容错能力

- **NestExtractOperator 验证增强** (`bd3d2c6`)
  - 验证 input_keys 和 output_keys 必须一一对应
  - 添加详细的调试日志
  - 检查输出列的值有效性

### Changed

- **Serving 模块重构** (`39a4196`)
  - 将 agent 相关的 serving 移到 `serving/agent/` 子目录
  - 更新 `serving/__init__.py` 导入路径
  - 移除旧的 `cli_openclaw_serving.py` 和 `sdk_nanobot_serving.py`（移至 agent 子目录）

- **Pipeline 并发控制优化** (`17f258f`)
  - `PartitionPipelineParallelRun` 改进并发提交逻辑
  - 防止 LLM Serving 并发竞争

- **项目名称更新** (`46ee92c`)
  - `open-dataflow` → `ya-dataflow`
  - 更新 CLI 输出和 PyPI API 地址

### Fixed

- **Chinese font 问题** (`31e1ae1`)
  - 修复 PDF 生成中的中文字体问题

- **File path 问题** (`9d6b7f3`, `a46e566`)
  - 修复文件路径验证逻辑
  - 确保所有文件路径以 `/workspace/` 开头

- **Verification 问题** (`cf0e77b`)
  - 修复任务验证逻辑
  - 改进验证反馈格式

- **Agent name 问题** (`1090495`)
  - 修复 agent 名称匹配逻辑

- **Empty query 问题** (`2ad44c5`)
  - 添加 query 非空验证

- **CSV gen 问题** (`ef27606`)
  - 修复 CSV 文件生成问题

- **Feedback 问题** (`76e1bd1`, `249c29a`, `5e01eea`, `c3c9464`)
  - 修复反馈生成和验证逻辑

- **Dict types 问题** (`bd3d2c6`)
  - 修复字典类型处理

### Removed

- **冗余依赖** (`46ee92c`)
  - 移除无用的 prompt 和依赖

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
