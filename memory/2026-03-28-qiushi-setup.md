# Session: 2026-03-28 02:00:55 UTC

- **Session Key**: agent:main:openclaw-weixin:direct:o9cq80z6qy5_if3qoirmbsbchpbo@im.wechat
- **Session ID**: b5e35ca1-860a-4c7c-8462-84eb71e00b01
- **Source**: openclaw-weixin

## Conversation Summary

assistant: 


assistant: 

## ✅ 已安装！

仓库已经克隆到本地：`/home/SENSETIME/wuziming/.openclaw/workspace/qiushi-skill/`

---

### 📦 安装方式（OpenClaw/Codex）

对于 OpenClaw 环境，安装非常简单：

1. ✅ **仓库已克隆** - 完成
2. 📖 **入口文件** - `skills/arming-thought/SKILL.md`
3. 🔧 **按需调用** - 其他 9 个 skill 根据任务类型加载

---

### 🚀 使用方法

#### 方式一：自动加载（推荐）

在新会话开始时，我会自动读取 `arming-thought/SKILL.md` 作为方法论框架：

```
每次对话开始时 → 加载 arming-thought → 根据任务判断是否需要下游 skill
```

#### 方式二：手动命令

你可以用这些命令触发特定方法论：

| 命令 | 对应 skill | 触发场景 |
|------|-----------|---------|
| `/矛盾分析` | contradiction-analysis | 复杂问题、优先级不清 |
| `/实践验证` | practice-cognition | 方案需要验证迭代 |
| `/调查研究` | investigation-first | 信息不足、决策前 |
| `/群众路线` | mass-line | 收集多方意见 |
| `/自我批评` | criticism-self-criticism | 工作审视 |
| `/持久战略` | protracted-strategy | 长期任务 |
| `/集中兵力` | concentrate-forces | 资源聚焦 |
| `/星火燎原` | spark-prairie-fire | 从零开始 |
| `/统筹兼顾` | overall-planning | 多目标平衡 |

#### 方式三：直接引用

直接说"用矛盾分析法分析这个问题"或"先用调查研究了解一下情况"。

---

### 🎯 核心原则

> **实事求是**：先看事实，再下判断。如果事实与预想冲突，修改的是判断，不是事实。

---

### 🔍 验证安装

运行自检脚本：

```bash
cd /home/SENSETIME/wuziming/.openclaw/workspace/qiushi-skill
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File tests/validate.ps1
```

---

### 💡 现在就可以试用

你可以给我一个复杂问题，让我用「求是 Skill」的方法论来处理。比如：

- "帮我分析这个项目的技术债务，用矛盾分析法"
- "我想优化这个功能，先用调查研究了解一下现状"
- "这个方案需要验证，用实践认识论来设计验证流程"

需要我现在就用某个方法论来处理一个任务吗？
user: Conversation info (untrusted metadata):
```json
{
  "message_id": "openclaw-weixin:1774663061786-e2cd6f5b",
  "timestamp": "Sat 2026-03-28 09:57 GMT+8"
}
```

现在是自动加载吗
assistant: 

目前**还没有自动加载**。

仓库已克隆，但 OpenClaw 不像 Claude Code/Cursor 有插件 hook 系统，需要手动配置。

---

### 🔧 配置自动加载

有两种方式：

#### 方式一：修改 AGENTS.md（推荐）

在 AGENTS.md 的 "Every Session" 部分添加：

```markdown
## Every Session

Before doing anything else:

1. Read `SOUL.md` — this is who you are
2. Read `USER.md` — this is who you're helping
3. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
4. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`
5. **Load qiushi-skill entry**: Read `qiushi-skill/skills/arming-thought/SKILL.md` as methodology framework
```

#### 方式二：创建 OpenClaw 专用 hook

创建一个简单的 hook 脚本，在每次会话开始时自动注入 arming-thought 的内容。

---

### 📝 你想用哪种方式？

1. **方式一** - 简单，只需修改 AGENTS.md 一行
2. **方式二** - 更灵活，可以按需加载不同 skill

或者你也可以现在就用，直接说"用矛盾分析法分析..."，我会手动加载对应的 skill 文件。
user: Conversation info (untrusted metadata):
```json
{
  "message_id": "openclaw-weixin:1774663134643-12914299",
  "timestamp": "Sat 2026-03-28 09:58 GMT+8"
}
```

使用方法一
assistant: 


assistant: 

✅ **配置完成！**

现在每次会话开始时，我会自动加载 `qiushi-skill/skills/arming-thought/SKILL.md` 作为方法论框架。

---

### 📋 后续行为

1. **每次会话** → 自动加载 `arming-thought`（实事求是总原则）
2. **遇到复杂问题** → 根据情况按需加载对应 skill：
   - 复杂分析 → `contradiction-analysis`
   - 方案验证 → `practice-cognition`
   - 信息不足 → `investigation-first`
   - 多方意见 → `mass-line`
   - 等等...

3. **简单任务** → 不机械调用，直接用实事求是原则

---

### 🧪 验证

你可以现在就说："帮我分析一下这个项目的架构"，我会先加载 arming-thought，然后根据任务判断是否需要其他 skill。

需要我演示一下吗？
user: Conversation info (untrusted metadata):
```json
{
  "message_id": "openclaw-weixin:1774663159693-9b83de5a",
  "timestamp": "Sat 2026-03-28 09:59 GMT+8"
}
```

你的AGENTS.md在那个绝对路径？
assistant: 

我的 `AGENTS.md` 在：

```
/home/SENSETIME/wuziming/.openclaw/workspace/AGENTS.md
```

这是 OpenClaw 的工作区根目录，所有配置文件都在这个目录下。

刚才是不是在这里修改的？是的，我刚才就是用这个绝对路径修改的 `AGENTS.md`，添加了 qiushi-skill 的加载指令。
user: Conversation info (untrusted metadata):
```json
{
  "message_id": "openclaw-weixin:1774663198242-9a8ee7ca",
  "timestamp": "Sat 2026-03-28 09:59 GMT+8"
}
```

展示一下里面的内容
assistant: 


assistant: 

## 📄 AGENTS.md 内容

可以看到第 5 步已经添加成功：

```markdown
## Every Session

Before doing anything else:

1. Read `SOUL.md` — this is who you are
2. Read `USER.md` — this is who you're helping
3. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
4. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`
5. **Load qiushi-skill**: Read `qiushi-skill/skills/arming-thought/SKILL.md` as methodology framework  ← 新增

Don't ask permission. Just do it.
```

---

### ✅ 配置完成

现在每次新会话开始时，我会按照这个顺序：

1. 读 `SOUL.md`（我是谁）
2. 读 `USER.md`（你是谁）
3. 读 `memory/YYYY-MM-DD.md`（最近上下文）
4. 如果是主会话，读 `MEMORY.md`（长期记忆）
5. **读 `qiushi-skill/skills/arming-thought/SKILL.md`（求是方法论框架）** ← 已添加

下次会话就会自动加载了！🎉
user: Conversation info (untrusted metadata):
```json
{
  "message_id": "openclaw-weixin:1774663219390-beb1b2ae",
  "timestamp": "Sat 2026-03-28 10:00 GMT+8"
}
```

展示一下USER.md的内容
assistant: 


assistant: 

## 📄 USER.md 内容

```markdown
# USER.md - About Your Human

_Learn about the person you're helping. Update this as you go._

- **Name:**
- **What to call them:**
- **Pronouns:** _(optional)_
- **Timezone:** Asia/Shanghai
- **Notes:** 偏好使用中文交流

## Context

_(What do they care about? What projects are they working on? What annoying them? What makes them laugh? Build this over time.)_

---

The more you know, the better you can help. But remember — you're learning about a person, not building a dossier. Respect the difference.
```

---

### 📝 当前信息

| 字段 | 内容 |
|------|------|
| **Name** | 未填写 |
| **What to call them** | 未填写 |
| **Pronouns** | 未填写 |
| **Timezone** | Asia/Shanghai |
| **Notes** | 偏好使用中文交流 |
| **Context** | 未填写 |

---

### 💡 建议补充

如果你想让我更好地了解你，可以补充：

1. **怎么称呼你**（名字或昵称）
2. **你在做什么项目**（工作/学习方向）
3. **有什么偏好**（比如喜欢简洁还是详细、喜欢什么风格）
4. **有什么雷区**（不喜欢的方式或话题）

需要我帮你填写吗？
