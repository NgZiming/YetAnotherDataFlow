# DataFlow 重构设计总结

> AI 生成内容

> 本文档是对当前版本与官方 v1.0.10 的设计差异总结，适合作为 README.md 的补充阅读。

---

## 核心改进概览

| 模块         | 官方 v1.0.10            | 当前版本                    | 核心优势                 |
| ------------ | ----------------------- | --------------------------- | ------------------------ |
| **Storage**  | 单文件 (1184 行)，无 S3 | 模块化 (~5000+ 行)，支持 S3 | 数据面/控制面分离        |
| **Pipeline** | 697 行，无并行          | 1225 行，Spark 风格         | 原生支持并行分片         |
| **Serving**  | 基础实现                | 增强版 + OpenClaw           | 支持重试/多模态/OpenClaw |
| **数据规模** | 内存一次性加载          | 流式处理                    | 支持 TB 级别数据         |

---

## 1. Storage 模块

[参考文档](dataflow/utils/storage/README.md)

### 1.1 对比优势

| 特性                      | 官方 v1.0.10     | 当前版本              |
| ------------------------- | ---------------- | --------------------- |
| 架构                      | 单文件，职责混合 | 模块化，职责分离      |
| 数据面/控制面             | 未分离           | 明确分离              |
| DataSource                | 无               | 支持 S3/HF/MS/本地    |
| MediaStorage/CacheStorage | 无               | 针对复杂存储环境抽象  |
| 分片处理                  | 无               | 原生支持              |
| 多步骤合并                | 无               | load_partition 取交集 |

### 1.2 为什么要这样改动？

| 改动                           | 原因                                                           |
| ------------------------------ | -------------------------------------------------------------- |
| DataSource 流式读取            | 真实场景有千万行/TB 级别数据，避免内存爆炸                     |
| DataSource 抽象                | 解决复杂环境的读写权限，企业里上下游数据保存位置和读写控制严格 |
| MediaStorage/CacheStorage 抽象 | 企业内存储环境复杂，需要统一管理媒体文件和缓存                 |
| 支持 S3                        | 官方还没有支持 S3，需要提前适配                                |
| 数据面/控制面分离              | 个人工程品味，融合 dataflow 原有设计的改进                     |

---

## 2. Serving 模块

### 2.1 对比优势

| 特性              | 官方 v1.0.10 | 当前版本           |
| ----------------- | ------------ | ------------------ |
| MediaStorage 集成 | 无           | 支持               |
| CLI 集成          | 无           | CLIOpenClawServing |
| API Key 校验      | 强制         | 可选               |

### 2.2 为什么要这样改动？

| 改动                              | 原因                                   |
| --------------------------------- | -------------------------------------- |
| APIVLMServing_openai 增加重试机制 | 提高 API 调用的稳定性                  |
| 集成 MediaStorage                 | 支持从 Storage 读取媒体文件，统一管理  |
| 新增 CLIOpenClawServing           | 通过 OpenClaw CLI 调用 Agent，支持并发 |
| API Key 校验改为可选              | 某些场景下不需要 API Key               |
| 增加 max_completion_tokens        | 控制生成长度                           |

---

## 3. Pipeline 模块

### 3.1 对比优势

| 特性               | 官方 v1.0.10 | 当前版本                     |
| ------------------ | ------------ | ---------------------------- |
| Workload 类        | 无           | 新增                         |
| 并行分片执行       | 手动处理     | PartitionPipelineParallelRun |
| 依赖管理           | 无           | 完整管理                     |
| execute_workload() | 无           | 单独方法                     |
| 断点续传           | 简单         | 完整支持                     |

### 3.2 为什么要这样改动？

| 改动                            | 原因                                                                                                                  |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 引入 Workload 类                | 从 Spark 中吸取的经验，更清晰地表示工作负载状态                                                                       |
| PartitionPipelineParallelRun 类 | 企业环境里同时部署了很多模型和购买了很多 api 服务，通过依赖分析识别不依赖彼此的请求，跨过原本约束尽快应对数据交付压力 |
| 完整的依赖管理                  | 支持复杂的多步骤 Pipeline，自动处理依赖关系                                                                           |

---

## 4. 实际效果

- **数据规模**：支持千万行级别、TB 级别数据
- **内存占用**：流式处理，内存占用恒定，与数据量无关
- **并行度**：分片级并发 + 算子级并发，最大化并行度
- **断点续传**：完整支持，故障恢复更可靠
- **API 稳定性**：重试机制提高成功率
- **OpenClaw 集成**：通过 CLI 调用 Agent，支持并发
- **S3 支持**：提前适配官方尚未支持的功能

---

## 5. 兼容性说明

### Operator 不受影响

**现有 Operator 代码无需修改！** Storage 模块的改动完全在控制面（Pipeline）和数据面（Storage）内部完成，Operator 的 `run()` 接口保持不变：

```python
# Operator 代码不受影响
class MyOperator(OperatorABC):
    def run(self, storage: StorageABC, **kwargs):
        df = storage.read()      # 接口不变
        # ... 处理逻辑 ...
        storage.write(df)        # 接口不变
```

### 不兼容的变更（仅影响 Pipeline 作者）
1. Storage 初始化需要传入 `data_source` 而非 `first_entry_file_name`
2. API 变更：`get_keys_from_dataframe` → `get_keys`
3. 分片流程：必须先调用 `split_input()` 才能 `read()`

### 向后兼容
1. `_partitions` 默认值为 1，保持向后兼容
2. 基本读写接口 (`read`, `write`) 保持不变

---

## 6. 快速参考

### Storage 使用示例

```python
# 1. 创建 DataSource
source = S3DataSource(endpoint, ak, sk, s3_paths, format_type="jsonl")

# 2. 创建 Storage
storage = FileStorage(data_source=source, cache_path="./cache")

# 3. 分片（必须首先调用）
storage.split_input(num_partitions=10)

# 4. 处理步骤 0
storage.batch_step = 0
storage.operator_step = 0
df = storage.read()  # 直接读 files[0]

# 5. 处理步骤 >0
storage.operator_step = 1
storage.load_partition(dependent_steps=[0])
df = storage.read()  # 返回 load_partition 的结果
```

### Pipeline 使用示例

```python
# 并行分片执行
pipeline = PartitionPipelineParallelRun(cache_storage, partitions=10)
pipeline.compile()
pipeline.forward(max_parallelism=4)  # 并发执行
```

### Serving 使用示例

```python
# OpenClaw CLI 集成
from dataflow.serving import create_openclaw_serving

serving = create_openclaw_serving(
    agent_id="main",
    model="custom/Qwen3.5-122B-A10B",
    max_workers=4,
)
responses = serving.generate_from_input(["问题 1", "问题 2"])
```
