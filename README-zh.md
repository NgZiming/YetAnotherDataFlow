[English](README.md) | 简体中文

---

DataFlow 是个好项目，但是我的工作来得太快了，我没时间等它实现我需要的功能。

等我适配好了我的环境，交付了我自己的项目，发现距离 v1.0.8 已经太远了，没有合回去的可能性。所以自己建了一个 repo。

主要差异：
1. 支持 S3
2. 并发数分片运行，依赖分析
3. 读写分离
4. openclaw serving

欢迎大家使用。

# DataFlow Pipeline 新手教程

> 本教程将带你从零开始，使用 DataFlow 构建一个完整的 AI 数据处理 Pipeline。

## 📚 目录

1. [环境准备](#1-环境准备)
2. [第一个 Pipeline：文本翻译](#2-第一个-pipeline 文本翻译)
3. [多步骤 Pipeline](#3-多步骤-pipeline)
4. [S3 数据源支持](#4-s3 数据源支持)
5. [常见问题](#5-常见问题)

---

## 1. 环境准备

### 1.1 安装 DataFlow

```bash
# 创建虚拟环境
conda create -n dataflow python=3.12
conda activate dataflow

# 安装 DataFlow
pip install open-dataflow
```

### 1.2 验证安装

```bash
dataflow -v
```

应该看到类似输出：
```
open-dataflow codebase version: 1.0.0
```

---

## 2. 第一个 Pipeline：文本翻译

### 2.1 准备数据

创建一个 `input.jsonl` 文件：

```json
{"id": 1, "raw_content": "Hello, world!"}
{"id": 2, "raw_content": "DataFlow is awesome!"}
{"id": 3, "raw_content": "Machine learning is fun"}
```

### 2.2 编写 Pipeline

创建 `translate_pipeline.py`：

```python
from dataflow.pipeline import PipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage


class TranslatePipeline(PipelineABC):
    def __init__(self):
        # 1. 创建进度存储（PipelineABC 需要）
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # 2. 创建 DataSource（支持多个路径）
        self.data_source = FileDataSource(
            paths=["./input.jsonl", "./input_dir/"],  # 文件和目录混合
            format_type="jsonl",
        )

        # 3. 创建 Storage（必须指定 id_key）
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",  # 必须指定！用于唯一标识每一行
            cache_path="./cache",
            cache_type="jsonl",
        )

        # 4. 创建 LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )

        # 5. 创建翻译算子
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请将以下内容翻译成中文：",
        )

    def forward(self):
        # 使用 storage.step() 自动管理分片和步骤
        self.translate_op.run(
            self.storage.step(),
            input_key="raw_content",
            output_key="translation",
        )


if __name__ == "__main__":
    # 创建 Pipeline
    pipeline = TranslatePipeline()

    # 编译（初始化）
    pipeline.compile()

    # 执行
    pipeline.forward()

    print("✅ 翻译完成！结果保存在 ./cache 目录")
```

### 2.3 运行 Pipeline

```bash
python translate_pipeline.py
```

### 2.4 查看结果

```bash
cat cache/partition_1.jsonl
```

输出示例：
```json
{"id": 1, "raw_content": "Hello, world!", "translation": "你好，世界！"}
{"id": 2, "raw_content": "DataFlow is awesome!", "translation": "DataFlow 太棒了！"}
{"id": 3, "raw_content": "Machine learning is fun", "translation": "机器学习很有趣"}
```

---

## 3. 多步骤 Pipeline

### 3.1 场景：翻译 + 润色 + 摘要

```python
from dataflow.pipeline import PipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage


class MultiStepPipeline(PipelineABC):
    def __init__(self):
        # 1. 创建进度存储
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # 2. 创建 DataSource
        self.data_source = FileDataSource(
            paths=["./input.jsonl"],
            format_type="jsonl",
        )

        # 3. 创建 Storage
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl",
        )

        # 4. 创建 LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )

        # 5. 创建算子
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请将以下内容翻译成中文：",
        )

        self.polish_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请润色以下中文文本，使其更自然流畅：",
        )

        self.summary_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请为以下文本生成一个简短摘要：",
        )

    def forward(self):
        # 步骤 0: 翻译
        self.translate_op.run(
            self.storage.step(),
            input_key="raw_content",
            output_key="translation",
        )

        # 步骤 1: 润色（自动依赖步骤 0）
        self.polish_op.run(
            self.storage.step(),
            input_key="translation",
            output_key="polished",
        )

        # 步骤 2: 摘要（自动依赖步骤 1）
        self.summary_op.run(
            self.storage.step(),
            input_key="polished",
            output_key="summary",
        )


if __name__ == "__main__":
    pipeline = MultiStepPipeline()
    pipeline.compile()
    pipeline.forward()
```

---

## 4. S3 数据源支持

### 4.1 从 S3 读取数据

```python
from dataflow.utils.storage import S3DataSource, S3Storage, FileCacheStorage
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.pipeline import PipelineABC


class S3Pipeline(PipelineABC):
    def __init__(self):
        # 1. 创建进度存储
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # 2. 创建 S3 DataSource
        self.data_source = S3DataSource(
            endpoint="https://s3.example.com",
            ak="YOUR_ACCESS_KEY",
            sk="YOUR_SECRET_KEY",
            s3_paths=["s3://your-bucket/input/"],  # 支持目录
            format_type="jsonl"
        )

        # 3. 创建 S3 Storage
        self.storage = S3Storage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl"
        )

        # 4. 创建 LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )

        # 5. 创建算子
        self.process_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请处理以下内容：",
        )

    def forward(self):
        # 使用 storage.step() 处理数据
        self.process_op.run(
            self.storage.step(),
            input_key="raw_content",
            output_key="result",
        )
```

---

## 5. 常见问题

### Q1: 如何处理超大文件（>100GB）？

使用 `PartitionPipelineParallelRun` 进行并行分片处理：

```python
from dataflow.utils.storage import S3DataSource, S3Storage, FileCacheStorage
from dataflow.pipeline import PartitionPipelineParallelRun


class LargeFilePipeline(PartitionPipelineParallelRun):
    def __init__(self, partitions: int):
        progress_storage = FileCacheStorage(cache_path="./cache")
        # 传入分片数量
        super().__init__(progress_storage, partitions)

        self.data_source = S3DataSource(
            endpoint="https://s3.example.com",
            ak="xxx", sk="xxx",
            s3_paths=["s3://bucket/huge_file.jsonl"],
            format_type="jsonl"
        )

        self.storage = S3Storage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl"
        )

    def forward(self):
        pass


pipeline = LargeFilePipeline(1000)
pipeline.compile()
pipeline.forward(max_parallelism=10)  # 10 个并发
```

### Q2: 如何断点续传？

```python
# PipelineABC 支持断点续传
pipeline.forward(resume_from_last=True)  # 从上次中断处继续
```

### Q3: DataSource 和 Storage 可以是不同的格式吗？

**可以！** DataSource 和 Storage 可以独立选择格式和存储类型：

```python
# 示例：从 S3 JSONL 读取，写入本地 Parquet
from dataflow.utils.storage import S3DataSource, FileStorage, FileCacheStorage
from dataflow.pipeline import PipelineABC


class ConvertPipeline(PipelineABC):
    def __init__(self):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # DataSource: S3 JSONL
        self.data_source = S3DataSource(
            endpoint="https://s3.example.com",
            ak="xxx", sk="xxx",
            s3_paths=["s3://bucket/input.jsonl"],
            format_type="jsonl"
        )

        # Storage: 本地 Parquet
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./output",
            cache_type="parquet"  # 输出格式可以是 Parquet
        )

    def forward(self):
        pass
```

**注意**：同一个 DataSource 的所有文件必须是**相同格式**。

### Q4: 如何自定义 Operator？

```python
from dataflow.operators import OperatorABC


class MyCustomOperator(OperatorABC):
    def run(self, storage, **kwargs):
        df = storage.read()

        # 自定义处理逻辑
        df['new_column'] = df['old_column'].apply(self.process)

        storage.write(df)

    def process(self, value):
        # 你的处理逻辑
        return value.upper()


# 使用
my_op = MyCustomOperator()
my_op.run(storage, input_key='text', output_key='upper_text')
```

### Q5: `id_key` 的作用是什么？

`id_key` 用于唯一标识每一行数据，支持：
- **断点续传**：跳过已处理的数据
- **去重**：避免重复处理
- **增量更新**：只处理新增/修改的数据

```python
# 如果数据没有 id 字段，可以使用其他唯一字段
self.storage = FileStorage(
    data_source=self.data_source,
    id_key="url",  # 使用 url 作为唯一标识
    cache_path="./cache",
    cache_type="jsonl",
)
```

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

---

## 版本历史

### [1.0.3] - 2026-04-02

#### Added

- **核心算子模块**
  - `JsonParseFilter`: JSON 解析和验证算子，支持字段类型检查、正则匹配、数值范围验证
  - `NestExtractOperator`: 嵌套 JSON 提取算子，支持点号路径和数组索引语法
  - `FormatStrPromptedAgenticGenerator`: 基于模板提示的 Agent 生成算子，支持传递文件内容数据
  - `FileContextGenerator`: 文件内容合成算子，根据文件路径和问题生成表格/文档/PPT/代码等内容

- **二进制文件生成系统**
  - `generate_binary_files.py`: 支持 11 种格式的测试文件生成
  - `CLIOpenClawServing` 增强：支持在 LLM 调用前注入二进制文件内容数据
  - 5 类 Prompt 模板：table/document/presentation/structured/text/code

- **依赖更新**
  - 新增 `openpyxl`, `python-pptx`, `reportlab`, `docx`

#### Fixed

- 文件验证和路径处理修复
- Pipeline 输入检查增强

### [1.0.2] - 2026-04-01

#### Fixed

- **Pipeline 输入键检查增强**
  - `PipelineABC._check_input_keys`: 添加空 `input_key_first_part` 检查
  - `PartitionPipelineParallelRun`: 跳过以 `.` 开头的 `key_para_name`

- **OpenClaw CLI Serving 超时处理**
  - timeout 从硬编码 30 秒改为使用传入的 `timeout` 参数
  - 超时异常从返回空字符串改为抛出异常

- **Storage schema 包含 id_key**
  - `FileStorage.get_schema` 和 `S3Storage.get_schema` 返回的 schema 包含 `id_key`

- **文件句柄泄漏修复**
  - `FileStorage._load_data_for_pruning`: 添加 try-finally 确保文件正确关闭

### [1.0.1] - 2026-03-31

#### Added

- **IdSynthesizer 抽象类**
  - 新增 `IdSynthesizer` 抽象基类，支持缺失 `id_key` 的自动合成
  - 实现 `UuidIdSynthesizer`（默认）和 `CounterIdSynthesizer`

- **OpenClaw CLI Serving 重构**
  - 改用预先创建的 worker agents，避免重复创建失败
  - 每次请求前执行 `/new` 创建新 session

- **Pipeline 并行检查优化**
  - `_check_completed_workloads` 改为多线程并行检查

#### Changed

- **Session 文件等待增强**
  - `load_session` 改为抛异常，不再返回 `None`
  - `_resolve_transcript_path` 超时从 15 秒延长到 60 秒

- **Worker agent 注册等待**
  - 创建 worker agent 后轮询检查是否注册成功

### [1.0.0] - 2026-03-30

#### Added

- **Storage 模块架构重构**
  - 重构为 6 个模块化文件
  - 数据面/控制面分离设计
  - DataSource 抽象类，支持本地文件、S3、HuggingFace、ModelScope

- **智能行数估算**
  - Parquet 格式 O(1) 复杂度读取 metadata
  - S3 Parquet 仅下载 footer (~64KB)

- **PartitionPipelineParallelRun** - 支持大规模数据并行处理

- **数据格式支持扩展**
  - Parquet、Pickle、JSON

- **S3 性能优化**
  - S3 Range 请求：Parquet 文件仅下载 footer

#### Changed

- **Pipeline 接口简化**
  - 使用 `storage.step()` 自动管理分片和步骤
  - 自动分片和依赖管理

#### Documentation

- 新增 `TUTORIAL.md` (中文) 和 `TUTORIAL-en.md` (英文)
- 更新 `README.md` 和 `README-zh.md`

---

<div align="center">

**有问题？查看 [GitHub Issues](https://github.com/OpenDCAI/DataFlow/issues) 或加入 [社区](#11-community--support)**

</div>
