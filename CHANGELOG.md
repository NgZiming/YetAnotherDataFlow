## [1.0.10] - 2026-04-24

### 🛠️ Improvements & Fixes
- **JsonParseFilter Enhancement**: Added support for nested field validation using dot-notation (e.g., `user.profile.name`), enabling deeper JSON structure verification.
- **Robustness**: Improved retry logic and streamlined bootstrap processes.
- **Versioning**: Updated versioning metadata.

---

## [1.0.9] - 2026-04-22


### 🏗️ Architecture & Refactoring
- **Agent Serving Decoupling**: Moved `AgentServingABC` and its related logic from `dataflow/core/llm_serving.py` to a dedicated interface file `dataflow/serving/agent/iface.py`.
- **Type Definition Simplification**: Removed `dataflow/core/types.py` and moved `OPERATOR_CLASSES` and `LLM_SERVING_CLASSES` type aliases directly into `dataflow/core/__init__.py` for flatter imports.

### ⚙️ Serving Optimizations
- **CLI Serving Cleanup**: In `cli_openclaw_serving.py`, replaced complex `subprocess.Popen` process group management (`os.setsid`/`os.killpg`) with concise `subprocess.run`, significantly reducing redundancy while maintaining timeout control.

### 🛠️ Robustness & Detail Improvements (`iface.py`)
- **Path Mapping Enhancement**: Improved the replacement logic for `/workspace/assets/` paths to ensure higher accuracy when mapping to the actual workspace.
- **Verification Parser Upgrade**: Enhanced `_parse_verification_result` to support multiple feedback markers (e.g., "反馈:", "feedback:") and added a fallback splitting mechanism to handle unstable LLM output formats.
- **Token Efficiency**: Reduced the truncation limit for new file contents in the verification prompt from 10,000 to 5,000 characters to optimize LLM token usage and response latency.

---

## [1.0.8] - 2026-04-16

### Added
- **New DataSource types**:
  - `GeneratorDataSource`: 生成器数据源，支持基础数据 + LLM 增强字段
    - 通过 `generator_fn` 提供基础数据
    - 通过 `prompt_templates` 配置 LLM 生成的字段
    - 支持批量调用 LLM 提升效率
  - `LLMGeneratorDataSource`: 纯 LLM 生成数据源
    - 完全由 LLM 生成数据，无需基础文件
    - 通过 `prompts` 配置每个字段的生成 prompt
    - 支持批量生成，`batch_size` 可配置
  - `create_data_source()` 工厂函数增强：
    - 支持 `source_type="generator"` 和 `source_type="llm_generator"`
    - 新增参数：`generator_fn`, `total_rows`, `prompts`, `num_rows`, `fields_from_base`

### Changed
- **Fast read optimizations**: Multiple improvements to JSON reading performance
- **Sorting removed**: Removed sorting logic for better performance in certain scenarios
- **Debug improvements**: Enhanced debugging capabilities
- **CLI OpenClaw Serving**: Updated to support new datasource integration

### Fixed
- **Skills creation**: Fixed issues with skills creation and initialization
- **Session ID handling**: Fixed session ID generation and management

### Commits
- `ae59b1b` - feat: new datasource (GeneratorDataSource + LLMGeneratorDataSource)
- `07fb68d` - feat: try wait
- `438f45f` - feat: fallback
- `2340276` - feat: remove sort
- `96d073f` - feat: debug
- `3a808a4` - feat: fast read
- `631b973` - feat: fast read
- `d99f2ca` - feat: faster json
- `446b2f6` - fix: skills create
- `31a979b` - fix: skills
- `aa00531` - feat: shorten
- `047dd04` - feat: clean up
- `b7b8434` - fix: session id
- `59c83b` - feat: system prompt
- `070cda8` - feat: wait for session write

---

## [1.0.7] - 2026-04-15

### Added
- **Agent creation support**: Added capability to create agents dynamically
- **Enhanced logging**: Improved logging infrastructure
- **File path handling**: Enhanced file path management

### Fixed
- **Chinese font**: Fixed Chinese font rendering issues
- **Verification**: Fixed verification logic
- **Feedback handling**: Fixed feedback processing
- **Dict types**: Fixed dictionary type handling
- **Agent name**: Fixed agent naming issues
- **Empty query**: Fixed empty query handling

### Changed
- **Serving refactoring**: Moved agent-related serving to agent/ subdirectory
- **Prompt cleanup**: Removed unnecessary prompts

### Commits
- `a781c1b` - chore: bump version to 1.0.7 and update Python requirements
- `46ee92c` - feat: remove useless prompt
- `31e1ae1` - fix: chinese font
- `0962a63` - feat: logging
- `9d6b7f3` - fix: file path
- `cf0e77b` - fix: verification
- `4eeaee8` - feat: create agent
- `9f03be9` - feat: test
- `249c29a` - fix: feedback
- `5e01eea` - fix: feedback
- `bd3d2c6` - fix: dict types
- `a46e566` - feat: file path
- `224d54b` - feat: params
- `1090495` - fix: agent name
- `788b6aa` - feat: test gen
- `e3a91e` - feat: files
- `018166e` - fix: import
- `cae191c` - feat: add skills
- `39a4196` - refactor(serving): 将 agent 相关的 serving 移到 agent/子目录
- `2ad44c5` - fix: empty query

---

## [1.0.6] - 2026-04-14

### Added
- **Warning system**: Added warning mechanisms
- **Cache configuration**: Made cache size configurable

### Changed
- **S3Storage refactoring**: Renamed `temp_dir` to `cache_dir` for consistency
- **Serving refactoring**: Refactored Serving class naming and added agent health check
- **LRUCacheManager**: Refactored cache architecture

### Fixed
- **Cache size**: Fixed cache size handling
- **Type issues**: Fixed various type-related issues

### Commits
- `691438c` - release: version 1.0.6
- `d39b7fd` - feat: warning
- `f5f0a22` - feat: rename
- `55a1b50` - refactor: 将 S3Storage 的 temp_dir 参数改为 cache_dir，统一命名
- `e696b26` - feat: 将 S3DataSource 和 S3Storage 的 cache_max_size_gb 改为可配置参数
- `722f1a5` - fix: cache size
- `524e002` - feat: cache size
- `ffca954` - refactor: 重构 Serving 类命名并添加 agent 健康检查
- `66b96e4` - fix: type
- `ac516d2` - refactor: 重构 LRUCacheManager 缓存架构

---

## [1.0.5] - 2026-04-13

(Previous version - see git tag v1.0.5 for details)
