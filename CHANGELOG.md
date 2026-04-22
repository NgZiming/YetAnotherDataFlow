# Changelog

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
