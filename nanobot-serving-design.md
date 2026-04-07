# NanobotServing 设计方案

## 📋 背景

当前 `CLIOpenClawServing` 通过调用 OpenClaw CLI 命令执行任务，存在以下问题：

| 问题 | 说明 |
|------|------|
| **subprocess 开销** | 每个请求启动新进程，延迟高 |
| **代码复杂** | ~400 行，包含大量 CLI 输出解析逻辑 |
| **依赖 CLI** | 必须安装 openclaw CLI 工具 |
| **Session 管理** | 需手动解析 JSONL 文件 |
| **并发模型** | 线程池 + 多 agent，资源占用高 |

## 🎯 目标

实现一个基于 nanobot Python SDK 的轻量级 Serving 类：

- ✅ 纯 Python 调用，无 subprocess 开销
- ✅ 代码量减少至 ~100 行
- ✅ 无需 CLI，通过代码创建配置
- ✅ 原生 async 并发，session_key 实现隔离
- ✅ 兼容 `CLIOpenClawServing` 接口

---

## 📊 方案对比

| 维度 | CLIOpenClawServing | NanobotServing（目标） |
|------|-------------------|----------------------|
| **代码量** | ~400 行 | ~100 行 |
| **调用方式** | subprocess CLI | 纯 Python async |
| **并发模型** | 线程池 + 多 agent | async + session_key |
| **启动开销** | ~1s/请求 | 无 |
| **内存占用** | 多 agent 进程 | 单进程 |
| **依赖** | openclaw CLI | nanobot-ai |
| **Session 隔离** | 手动解析文件 | 内置 session_key |

---

## 🏗️ 架构设计

### 核心类结构

```
NanobotServing
├── config_path        # 配置文件路径
├── workspace          # 工作目录
├── model              # 模型名称
├── provider           # LLM Provider
├── max_workers        # 并发数
├── timeout            # 超时时间
├── max_retries        # 最大重试次数
├── bot                # Nanobot 实例
│
├── _create_config()   # 自动创建配置文件
├── _execute_single()  # 执行单个查询（带重试）
├── generate_async()   # 异步并发执行
└── generate_from_input()  # 同步接口（兼容）
```

### 配置创建流程

```
NanobotServing.__init__()
    ↓
检查 config_path 是否存在
    ↓
不存在 → _create_config()
    ↓
创建目录（config_path.parent, workspace）
    ↓
写入 JSON 配置（agents.defaults, providers）
    ↓
Nanobot.from_config(config_path, workspace)
```

### 请求执行流程

```
generate_from_input(user_inputs, input_files_data)
    ↓
asyncio.run(generate_async(...))
    ↓
为每个输入创建 session_key = f"serving-{id(self)}-{i}"
    ↓
并发执行（Semaphore 限制 max_workers）
    ↓
_execute_single(session_key, query, files)
    ↓
    ├─ 生成文件到 workspace（如有）
    ├─ bot.run(query, session_key=session_key)
    └─ 重试（超时/异常，最多 max_retries 次）
    ↓
返回结果列表
```

---

## 📦 依赖

```txt
# 核心依赖
nanobot-ai>=0.1.5

# 可选（进度条显示）
tqdm>=4.66.0
```

**Python 版本要求：** ≥3.11（nanobot 要求）

---

## 📝 API 设计

### 构造函数

```python
NanobotServing(
    config_path: str = "~/.nanobot/config.json",
    workspace: str = "~/.nanobot/workspace",
    model: str = "vllm//data/share/models/Qwen3.5-122B-A10B/",
    provider: str = "vllm",
    max_workers: int = 4,
    timeout: int = 300,
    max_retries: int = 3,
    auto_create_config: bool = True,
    extra_skills_dirs: Optional[List[str]] = None,  # 新增：额外技能目录
)
```

### 主要方法

| 方法 | 说明 |
|------|------|
| `generate_from_input(inputs, system_prompt, json_schema, files)` | 同步接口（兼容 CLIOpenClawServing） |
| `generate_async(inputs, files)` | 异步接口（推荐） |
| `cleanup()` | 清理资源 |

### 兼容接口

```python
# 与 CLIOpenClawServing 完全兼容
results = serving.generate_from_input(
    user_inputs=["问题 1", "问题 2"],
    system_prompt="",      # 不使用
    json_schema={},        # 不使用
    input_files_data=[{}, {}],
)
```

---

## 🔐 配置示例

### 自动生成的 config.json

```json
{
  "agents": {
    "defaults": {
      "workspace": "/path/to/workspace",
      "model": "vllm//data/share/models/Qwen3.5-122B-A10B/",
      "provider": "vllm",
      "timezone": "Asia/Shanghai",
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "providers": {
    "vllm": {
      "apiKey": "",
      "apiBase": ""
    }
  },
  "tools": {
    "web": { "enable": true },
    "exec": { "enable": true, "timeout": 60 },
    "restrictToWorkspace": false
  }
}
```

### 自定义 Provider

```python
# OpenAI
serving = NanobotServing(
    provider="openai",
    model="gpt-4-turbo",
)
# 需手动编辑 config.json 添加 API key

# Anthropic
serving = NanobotServing(
    provider="anthropic",
    model="claude-3-5-sonnet",
)

# 自定义 vLLM 端点
serving = NanobotServing(
    provider="vllm",
    model="Qwen3.5-122B",
)
# 需手动编辑 config.json 设置 apiBase
```

### 使用外部技能目录

```python
# 方式 1：直接指定 OpenClaw 技能目录
serving = NanobotServing(
    workspace="~/.nanobot/workspace",
    extra_skills_dirs=[
        "/home/SENSETIME/wuziming/.openclaw/workspace/qiushi-skill/skills",
    ],
)

# 方式 2：多个技能目录
serving = NanobotServing(
    workspace="~/.nanobot/workspace",
    extra_skills_dirs=[
        "/path/to/skills/a",
        "/path/to/skills/b",
    ],
)

# 技能会被符号链接到 workspace/skills/<name>
# nanobot 会自动加载这些技能
```

---

## ⚠️ 注意事项

1. **Python 版本** - 需要 Python ≥3.11
2. **nanobot 版本** - 建议 `>=0.1.5`（Python SDK 正式版）
3. **并发限制** - 使用 Semaphore 限制并发数，避免资源耗尽
4. **超时控制** - 使用 `asyncio.wait_for` 控制单次请求超时
5. **重试策略** - 指数退避（1s, 2s, 4s...）
6. **文件处理** - 文件生成逻辑需复用现有 `dataflow.utils.generate_binary_files`

---

## 📁 文件组织

```
dataflow/
├── serving/
│   ├── cli_openclaw_serving.py   # 现有实现（保留）
│   ├── nanobot_serving.py        # 新实现
│   └── __init__.py               # 导出两个类
```

### __init__.py

```python
from .cli_openclaw_serving import CLIOpenClawServing, create_openclaw_serving
from .nanobot_serving import NanobotServing

__all__ = [
    "CLIOpenClawServing",
    "create_openclaw_serving",
    "NanobotServing",
]
```

---

## 🧪 测试计划

### 功能测试

| 测试项 | 说明 |
|--------|------|
| 配置自动创建 | 检查 config.json 和 workspace 目录是否生成 |
| 单请求执行 | 单个输入返回正确结果 |
| 并发执行 | 多个输入并发处理，结果顺序正确 |
| 超时控制 | 超时请求正确失败 |
| 重试机制 | 失败请求自动重试 |
| 文件处理 | input_files_data 正确生成文件 |
| Session 隔离 | 不同 session_key 独立对话历史 |

### 性能对比

| 指标 | CLIOpenClawServing | NanobotServing |
|------|-------------------|----------------|
| 单请求延迟 | ~2-3s | ~1-2s |
| 并发 4 请求 | ~4-6s | ~2-3s |
| 内存占用 | ~500MB | ~200MB |

---

## 📅 实施计划

1. **Phase 1** - 实现 `NanobotServing` 核心类
2. **Phase 2** - 编写单元测试
3. **Phase 3** - 性能对比测试
4. **Phase 4** - 文档完善
5. **Phase 5** - 集成到 dataflow pipeline

---

## 📚 参考资料

- [nanobot GitHub](https://github.com/HKUDS/nanobot)
- [nanobot Python SDK 文档](https://github.com/HKUDS/nanobot/blob/main/docs/PYTHON_SDK.md)
- [nanobot 配置 Schema](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/schema.py)
- [CLIOpenClawServing 实现](./cli_openclaw_serving.py)

---

*文档创建时间：2026-04-07*
*作者：NanobotServing 设计团队*
