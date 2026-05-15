## [1.0.15] - 2026-05-15

### 🔄 Major Architecture Changes

#### **Removal of Async/Await Pattern**
- **Complete Synchronization**: Removed all `async/await` patterns from User Simulator module.
  - Converted `LLMClientABC.generate()`, `UserSimulatorABC.run()`, `UserStage.execute()`, and `UserStep.execute()` to synchronous methods
  - Replaced `asyncio.gather()` with `ThreadPoolExecutor` for concurrent file processing
  - Maintained parallel execution for I/O-bound operations while simplifying the codebase
  - **Affected Files**:
    - `dataflow/core/agentic/user.py`: Core abstract interfaces
    - `dataflow/serving/agent/user/` (v1): `simulator.py`, `perception.py`, `understanding.py`, `decision.py`
    - `dataflow/serving/agent/user_v2/` (v2): `simulator.py`, `perception.py`, `understanding.py`, `decision.py`
  - **Benefits**:
    - Simplified codebase with synchronous programming model
    - Easier debugging and testing
    - Better integration with synchronous LLM clients (e.g., `requests`)
    - Thread-based concurrency maintains performance for I/O operations

### 🤖 LLM Client & Multimodal Support

#### **Enhanced LLMClientAdapter**
- **Multimodal Input Support**: Added native support for text + image inputs.
  - New `MessageContent` types: `TextContent`, `ImageContent`
  - `prompt` parameter accepts either plain text or list of message dicts
  - Supports OpenAI-compatible multimodal message format with base64-encoded images
  - Example usage:
    ```python
    prompt = [
        {"role": "user", "type": "text", "text": "Describe this image"},
        {"role": "user", "type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
    ```

#### **Embedding Support**
- **New Method**: Added `generate_embedding()` to `LLMClientABC` interface.
  - Returns vector representations for text inputs
  - Supports config overrides for model selection
  - Implements OpenAI-compatible `/embeddings` endpoint

#### **Code Refactoring**
- **Unified Client Architecture**: Consolidated LLM client implementations.
  - Removed deprecated `dataflow/serving/agent/user/llm_client.py`
  - Introduced `dataflow/serving/llm_client.py` as standard client implementation
  - Updated `LLMClientAdapter` to handle both text-only and multimodal inputs
  - Streamlined `api_llm_serving_request.py` and `api_vlm_serving_request.py`

### 🎥 Vision Language Model (VLM) Support

#### **VLM Serving Infrastructure**
- **Initial Implementation**: Added VLM request serving capabilities.
  - Created `api_vlm_serving_request.py` with full VLM API support
  - Integrated multimodal message handling
  - Supports image encoding and base64 transmission
  - Subsequent refinements reduced code complexity by ~75%

### 📊 Statistics

- **17 files changed**
- **~1,400 insertions(+), ~1,300 deletions(-)**
- **Net reduction**: Simplified codebase through async removal and client unification

---

## [1.0.14] - 2026-05-12

### ⚙️ Operator Enhancements

- **FormatStrPromptedGenerator JSON Schema Support**: Added native JSON schema validation with `to_dict` flag.
  - New `to_dict` parameter: when enabled, parses LLM output as JSON and validates against `json_schema`
  - Automatically filters out samples that fail schema validation
  - Stores both raw output and parsed dict in dataframe
  - Eliminates need for external `JsonParseFilter` when using structured output

### 🔧 LLM Client & Structured Output

- **LLMClientABC Interface Enhancement**: Added `json_schema` parameter to `generate()` method.
  - `StepSchema` now includes optional `json_schema` field for structured output
  - `UserStep` passes `json_schema` from schema to LLM client
  - `LLMClientAdapter` implements JSON Schema response format for OpenAI-compatible APIs
    - Uses `response_format: {type: "json_schema", json_schema: {...}}` payload
    - Enables strict schema validation at the LLM level

### 🧠 UserSimulator V2 Improvements

- **DecisionStageV2**: Added JSON schema constraints for all three steps.
  - `DialogueStrategy`: Enforces `strategy_type` enum (提问/提供反馈/要求澄清/表达不满/表示满意/催促/其他)
  - `PersonaStyle`: Enforces `length_hint` enum (short/medium/long)
  - `FinalResponse`: Enforces `judgment` enum (completed/in_progress/aborted)
  - Updated prompt禁令 to explicitly forbid internal management terminology (里程碑，Stage, Step, Task State, etc.)

- **UnderstandingStageV2**: Added JSON schema for structured outputs.
  - `MilestoneStatus`: Enforces milestone `status` enum (completed/in_progress/not_started/blocked)
  - `TaskState`: Enforces `final_status` enum (CONTINUE/FINISHED/ABORTED) and `emotional_tone` enum

- **PerceptionStageV2**: Added JSON schema for all three sensors.
  - `FileContext`: Enforces required fields (path, fact, evidence_snippet, relevance)
  - `AgentContext`: Enforces `reasoning_pattern` enum and `is_looping` boolean
  - `DialogueContext`: Enforces `emotional_tone` enum (satisfied/dissatisfied/confused/urgent/neutral)

### 🛠️ Stability & Robustness

- **SDKNanobotServing**: Added 600s timeout to `bot.run()` to prevent hanging queries.
  - Uses `asyncio.wait_for()` wrapper around bot execution
  - Prevents indefinite blocking on stuck agent sessions

### 📦 Dependencies

- Added `jsonschema>=4.0.0` to requirements.txt for JSON schema validation in FormatStrPromptedGenerator.

### 📊 Statistics

- **8 files changed**
- **271 insertions(+), 19 deletions(-)**
- Compared to tag `v1.0.13`

---

## [1.0.13] - 2026-05-11

### 🚀 Major Features
- **UserSimulator V2**: Upgraded to an evidence-driven cognitive architecture, separating `global_context` and `data_pool` to enhance state tracking and reasoning.
- **Cognitive Pipeline Enhancements**: Refactored `TaskState` to pass emotional fields, allowing the simulator to express varied tones (satisfied, confused, urgent, etc.).
- **Nanobot Serving Improvements**: Integrated `SDKNanobotServing` with detailed magenta-colored thread logging and strict `AgentHook` protocol adherence to prevent `AttributeError`.

### 🔧 Bug Fixes & Refinements
- **Nanobot Stability**: Fixed various issues in `SDKNanobotServing` related to iteration hooks and function missing errors.
- **Skill Management**: Fixed issues with skill copying and registration.
- **Output Formatting**: Refined output formats and fixed template/type inconsistencies.
- **Persona Integration**: Enhanced persona-driven responses in the simulator.

## [1.0.12] - 2026-05-08

### 🚀 Major Features

- **Full-fledged UserSimulator Implementation**: Introduced a robust, three-stage cognitive architecture for simulating user behavior during agent trajectories.
  - **Perception Stage**: Implements `FileSensor`, `AgentSensor`, and `DialogueSensor` to compress raw environment data, agent actions, and conversation history into structured contexts.
  - **Understanding Stage**: Implements `MilestoneMatcher`, `ProgressEvaluator`, and `TaskSynthesizer` to track task progress against milestones and assess quality/risks.
  - **Decision Stage**: Synthesizes perceptions and understanding into a final user response (feedback or completion judgment) based on a dialogue strategy and user persona.
- **Standardized Agent Serving Interface**: Introduced `AgentServingABC` to unify the lifecycle of agent execution, including workspace management, multi-round verification loops, and concurrent task scheduling.

### 🏗️ Architecture & Refactoring

- **Resource Path Alignment**: Refactored file preparation logic to preserve original directory hierarchies within the workspace, eliminating "missing file" loops caused by path flattening.
- **Robust JSON Parsing**: Integrated `json_repair` into the simulator's parsing logic to handle malformed LLM outputs, significantly reducing simulator crashes.
- **Flexible LLM Integration**: Added `LLMClientAdapter` for standardized REST communication with the simulator's backend LLM.

### 🔧 Bug Fixes

- **Pydantic Constraint Relaxation**: Increased `max_length` for `strategy_details` from 300 to 1000 characters to resolve `ValidationError` during complex decision making.
- **Path Cognitive Dissonance**: Fixed issues where agents could not find files because the serving layer had modified their paths.

### 📊 Statistics

- **14 files changed**
- **2381 insertions(+), 1113 deletions(-)**
- Compared to tag `v1.0.11`

---

## [1.0.11] - 2026-05-04

### 🚀 Major Features

- **Milestones Support**: Added support for milestones in agent trajectories, enabling structured multi-step task planning and verification.
  - New `input_milestones_key` parameter in `FormatStrPromptedAgenticGenerator` for per-task milestone injection
  - Milestones are now passed through `verification_prompt_templates` to support different verification strategies per task
  - Integration with `AgentServingABC` to handle milestone-aware verification prompts

### 🏗️ Architecture & Refactoring

- **Trajectory Format Simplification**: Removed `files_created` and `errors` fields from `TrajectoryDict` and `MessageDict` TypedDict definitions.
  - Simplified trajectory structure in `dataflow/core/llm_serving.py`
  - Error handling now managed externally rather than embedded in trajectory
  - More focused trajectory representation on core message flow

- **Retry Logic Restructuring**: Moved state variable initialization inside the retry loop in `AgentServingABC._execute_single_task_with_verification()`.
  - Ensures each retry attempt starts fresh from round 0
  - Prevents state contamination across retry attempts
  - More predictable retry behavior

- **Verification Prompt Flexibility**: Changed `verification_prompt_template` parameter to `verification_prompt_templates` (list) in `AgentServingABC.generate_from_input()`.
  - Supports per-task custom verification prompts
  - Each task can have its own milestone-aware verification template
  - Backward compatible with single template usage

### ⚙️ Operator Enhancements

- **FileContextGenerator Improvements**:
  - Added support for `.log` file format in addition to `.txt`
  - New `format` field in output JSON to distinguish between `txt` and `log` formats
  - Improved validation: check file format support before adding to generation queue
  - Better error handling: validate entire row before processing any files in that row
  - Updated prompts with clearer examples for both TXT and LOG formats

- **FormatStrPromptedAgenticGenerator**: 
  - Added `input_milestones_key` parameter for milestone injection
  - Milestone-aware verification prompt generation using `str.replace()` instead of `.format()` to avoid issues with missing placeholders
  - Each row can have different milestones, resulting in different verification prompts

### 🔧 Bug Fixes

- **Template Replacement**: Fixed verification prompt template replacement to use `str.replace()` instead of `.format()` in `AgentServingABC`.
  - Prevents `KeyError` when template has missing placeholders
  - More robust handling of partial template substitution

- **JSONL File Filtering**: Fixed transcript file discovery in `CLIOpenClawServing._resolve_transcript_paths()`.
  - Now excludes `*.trajectory.jsonl` files from candidates
  - Only considers actual session transcript files

- **Type Annotations**: Fixed type annotations throughout the codebase.
  - Changed `MessageDict` and `TrajectoryDict` from `total=False` to `total=True`
  - Added proper type hints for message formatting in `CLIOpenClawServing`
  - Consistent use of `MessageDict` type across the codebase

- **System Message Handling**: Simplified system message insertion in `CLIOpenClawServing._format_messages()`.
  - Removed `has_system_message` flag logic
  - Always insert system message at the beginning
  - Cleaner message formatting logic

### 🧹 Code Quality

- **Import Cleanup**: Removed unused `import os` in `format_str_prompted_agentic_generator.py`, replaced with `import json`.
- **Type Hint Improvements**: Added `MessageDict` import and usage in `cli_openclaw_serving.py`.
- **Return Value Standardization**: Ensured `CLIOpenClawServing._execute_single_task()` returns complete `TrajectoryDict` with all required fields.

### 📝 Commits

- `982fb41` - fix: template replace
- `a2c3092` - fix: remove extra jsonl
- `3a819ea` - fix: remove extra jsonl
- `72eeff4` - feat: add milestones
- `72786d3` - fix: type
- `d4ee346` - feat: format
- `22f08f7` - fix: filename
- `b324017` - fix: missing import

### 📊 Statistics

- **5 files changed**
- **127 insertions(+), 62 deletions(-)**
- Compared to commit `a673e96` (v1.0.10 release)

---

## [1.0.10] - 2026-04-29

### 🚀 Major Features

- **Milestones Support**: Added support for milestones in agent trajectories, enabling structured multi-step task planning and verification.
  - New `input_milestones_key` parameter in `FormatStrPromptedAgenticGenerator` for per-task milestone injection
  - Milestones are now passed through `verification_prompt_templates` to support different verification strategies per task
  - Integration with `AgentServingABC` to handle milestone-aware verification prompts

- **Enhanced JsonParseFilter**: Added support for nested field validation using dot-notation paths (e.g., `user.profile.name`).
  - New `_get_nested_value()` method for traversing nested JSON structures
  - All validation checks (required fields, type checks, value checks, patterns, ranges) now support nested paths
  - Enables deeper JSON structure verification without flattening

### 🏗️ Architecture & Refactoring

- **Trajectory Format Simplification**: Removed `files_created` and `errors` fields from `TrajectoryDict` and `MessageDict` TypedDict definitions.
  - Simplified trajectory structure in `dataflow/core/llm_serving.py`
  - Error handling now managed externally rather than embedded in trajectory
  - More focused trajectory representation on core message flow

- **Retry Logic Restructuring**: Moved state variable initialization inside the retry loop in `AgentServingABC._execute_single_task_with_verification()`.
  - Ensures each retry attempt starts fresh from round 0
  - Prevents state contamination across retry attempts
  - More predictable retry behavior

- **Verification Prompt Flexibility**: Changed `verification_prompt_template` parameter to `verification_prompt_templates` (list) in `AgentServingABC.generate_from_input()`.
  - Supports per-task custom verification prompts
  - Each task can have its own milestone-aware verification template
  - Backward compatible with single template usage

### ⚙️ Operator Enhancements

- **FileContextGenerator Improvements**:
  - Added support for `.log` file format in addition to `.txt`
  - New `format` field in output JSON to distinguish between `txt` and `log` formats
  - Improved validation: check file format support before adding to generation queue
  - Better error handling: validate entire row before processing any files in that row
  - Updated prompts with clearer examples for both TXT and LOG formats

- **FormatStrPromptedAgenticGenerator**: 
  - Added `input_milestones_key` parameter for milestone injection
  - Milestone-aware verification prompt generation using `str.replace()` instead of `.format()` to avoid issues with missing placeholders
  - Each row can have different milestones, resulting in different verification prompts

### 🔧 Bug Fixes

- **Template Replacement**: Fixed verification prompt template replacement to use `str.replace()` instead of `.format()` in `AgentServingABC`.
  - Prevents `KeyError` when template has missing placeholders
  - More robust handling of partial template substitution

- **JSONL File Filtering**: Fixed transcript file discovery in `CLIOpenClawServing._resolve_transcript_paths()`.
  - Now excludes `*.trajectory.jsonl` files from candidates
  - Only considers actual session transcript files

- **Bootstrap Removal**: Removed `BOOTSTRAP.md` from core workspace files in `CLIOpenClawServing`.
  - Avoids repeated initialization prompts for each request
  - Cleaner workspace structure

- **Type Annotations**: Fixed type annotations throughout the codebase.
  - Changed `MessageDict` and `TrajectoryDict` from `total=False` to `total=True`
  - Added proper type hints for message formatting in `CLIOpenClawServing`
  - Consistent use of `MessageDict` type across the codebase

- **System Message Handling**: Simplified system message insertion in `CLIOpenClawServing._format_messages()`.
  - Removed `has_system_message` flag logic
  - Always insert system message at the beginning
  - Cleaner message formatting logic

### 🧹 Code Quality

- **Removed Redundant Code**:
  - Removed `_execute_single_async()` method from `SDKNanobotServing` (now uses parent class implementation)
  - Removed duplicate `_cleanup_execution_context()` in `SDKNanobotServing`
  - Cleaned up extra jsonl files from previous commits

- **Improved Session File Reading**: Enhanced `SDKNanobotServing._read_session_file()` to extract full trajectory from session files.
  - Maps Nanobot format to `TrajectoryDict` format
  - Includes `round`, `role`, `content`, `thought`, `tool_calls`, `tool_results`, `session_id` fields
  - Fallback to single message if session file is missing

- **Better Logging**: Added more informative log messages for milestone injection and skill loading in `FormatStrPromptedAgenticGenerator`.

### 📝 Documentation

- Updated CHANGELOG.md with comprehensive changes for v1.0.10
- Added detailed commit history reference

### 🔄 Version Update

- Bumped version from 1.0.9 to 1.0.10

---

## [1.0.9] - 2026-04-22

### 🏗️ Architecture & Refactoring
- **Agent Serving Decoupling**: Moved `AgentServingABC` and its related logic from `dataflow/core/llm_serving.py` to a dedicated interface file `dataflow/serving/agent/iface.py`.
- **Type Definition Simplification**: Removed `dataflow/core/types.py` and moved `OPERATOR_CLASSES` and `LLM_SERVING_CLASSES` type aliases directly into `dataflow/core/__init__.py` for flatter imports.

### ⚙️ Serving Optimizations
- **CLI Serving Cleanup**: In `cli_openclaw_serving.py`, replaced complex `subprocess.Popen` process group management (`os.setsid`/`os.killpg`) with concise `subprocess.run`, significantly reducing redundancy while maintaining timeout control.

### 🛠️ Robustness & Detail Improvements (`iface.py`)
- **Path Mapping Enhancement**: Improved the replacement logic for `/workspace/assets/` paths to ensure higher accuracy when mapping to the actual workspace.
- **Verification Parser Upgrade**: Enhanced `_parse_verification_result` to support multiple feedback markers (e.g., \"反馈:\", \"feedback:\") and added a fallback splitting mechanism to handle unstable LLM output formats.
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
    - 支持 `source_type=\"generator\"` 和 `source_type=\"llm_generator\"`
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
- `9f01be9` - feat: test
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
