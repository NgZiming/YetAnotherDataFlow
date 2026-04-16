# ya-dataflow

**ya-dataflow** 是一个现代化的、以数据为中心的 AI 框架，旨在编排面向大语言模型 (LLM) 和视觉语言模型 (VLM) 的复杂、大规模工作流。

它提供了一个统一、模块化且高度可扩展的架构，用于管理 AI 数据处理的全生命周期—从原始数据的摄取、复杂的多模态推理，到高吞吐量的轨迹合成以及大规模评估。

---

## 🌟 核心理念：以数据为中心 (Data-Centric AI)

在大模型时代，瓶颈不再仅仅是模型容量，而是喂养模型的**数据流水线**的质量与规模。**ya-dataflow** 的设计初衷就是解决这一问题：通过将数据视为“一等公民”，提供一个强大的引擎，将原始、非结构化的数据转化为用于训练、微调和评估的高质量、结构化数据集。

---

## ✨ 核心能力

### 🏗️ 模块化算子架构 (Modular Operator Architecture)
`ya-dataflow` 的核心是高度可扩展的算子设计。无论是简单的文本清洗步骤，复杂的 RAG 检索，还是复杂的 VLM 推理过程，都可以被实现为一个标准的 `Operator`。这使得构建复杂的流水线变得像搭积木一样简单。

### 🚀 可扩展的流水线执行 (Scalable Pipeline Execution)
针对海量工作负载设计，`ya-dataflow` 支持先进的执行策略，如 `PartitionPipelineParallelRun`。它能够在分布式环境中实现高吞吐量的并行处理，足以应对数十亿 Token 或数百万多模态资产的处理需求。

### 🧪 高保真合成数据生成 (High-Fidelity Synthetic Data Generation)
利用强大的二进制和结构化文件生成系统，`ya-dataflow` 可以合成多样化的数据集—包括 JSON、XML、Markdown、HTML 以及复杂的二进制格式（如 PDF、XLSX 和 PPTX）—为构建稳健的测试和训练环境提供支持。

### 🤖 智能体生态集成 (Agentic Ecosystem Integration)
与 **OpenClaw** 和 **Nanobot** 生态系统深度集成。通过 `CLIOpenClawServing` 和 `NanobotServing`，您的数据流水线可以转化为可被自主智能体动态调用和编排的智能能力。

---

## 🚀 Why ya-dataflow? (高级特性)

不同于仅处理简单任务的工具，`ya-dataflow` 是为**生产级、大规模 AI 数据工程**而生的。

### 💎 企业级数据管理
- **云原生存储**：原生、无缝支持 **S3** 及其他主流云存储。
- **读写分离**：通过解耦 `DataSource` 与 `Storage` 层，实现极高的灵活性与控制力。
- **智能缓存机制**：内置先进的缓存管理 (`CacheStorage`)，极大优化 I/O 并加速重复任务。

### 💎 生产级可靠性
- **断点续传 (Checkpointing)**：内置完备的进度管理。即使是运行数天的超大规模任务，中断后也能从上次位置快速恢复。
- **细粒度并行化**：超越了简单的任务并行，通过**分片级并行 (Partition-level parallelism)**，支持将数据集拆分为极细粒度的单元进行高吞吐处理。

### 💎 智能体联动能力
- **OpenClaw 深度集成**：通过 `CLIOpenClawServing` 桥接智能体推理与重型数据流水线。
- **Nanobot 就绪**：集成 `NanobotServing`，可在 Nanobot SDK 生态系统中实现轻量级、高性能的服务化能力。

---

## 🚀 Getting Started

### 1. 安装

安装核心框架：
```bash
pip install ya-dataflow
```

根据具体场景安装扩展包：
```bash
# 用于 RAG 工作流
pip install ya-dataflow[rag]

# 用于多模态 (VLM) 和 PDF 处理
pip install ya-dataflow[pdf2vqa]

# 用于 LLM 服务与大规模评估
pip install ya-dataflow[vllm,eval]

# 用于代码与数学推理任务
pip install ya-dataflow[code,reasoning]
```

### 2. 基础用法 (Python API)

在生产环境中，您通过继承 `PartitionPipelineParallelRun` 并实现 `forward` 方法来定义流水线，从而编排您的算子。

```python
from dataflow.pipeline import PartitionPipelineParallelRun
from dataflow.operators.core_text import TextCleaningOperator
from dataflow.utils.storage import FileDataSource, FileStorage, FileCacheStorage
from dataflow.serving.api_llm_serving_request import APILLMServing_request

class MyProductionPipeline(PartitionPipelineParallelRun):
    def __init__(self, source: FileDataSource, storage: FileStorage, llm_serving: APILLMServing_request):
        # 1. 初始化 CacheStorage (关键：不能为空)
        cache_storage = FileCacheStorage(cache_path="./cache")
        
        # 2. 初始化基类并传入 cache_storage 与明确的分片数
        super().__init__(cache_storage=cache_storage, partitions=10)
        
        self.storage = storage
        self.llm_serving = llm_serving
        
        # 定义算子
        self.clean_op = TextCleaningOperator(self.llm_serving)
        self.refine_op = SomeRefineOperator(self.llm_serving)

    def forward(self):
        # 步骤 1: 清洗原始文本
        # .step() 获取当前分片的数据
        self.clean_op.run(
            self.storage.step(),
            input_key="raw_text",
            output_key="cleaned_text"
        )

        # 步骤 2: 基于清洗后的文本进行精炼 (依赖: cleaned_text)
        self.refine_op.run(
            self.storage.step(),
            input_key="raw_text",
            output_key="final_result",
            input_prev_1="cleaned_text" 
        )

# 使用示例
source = FileDataSource(paths=["./input.jsonl"])
storage = FileStorage(data_source=source, id_key="id", cache_path="./cache")
llm = APILLMServing_request(api_url="...", model_name="...")

pipeline = MyProductionPipeline(source, storage, llm)
pipeline.compile()
pipeline.run()
```

### 4. 新增：生成器数据源 (v1.0.8+)

无需预先存在的文件，动态生成数据。

#### GeneratorDataSource - 基础数据 + LLM 增强

当您有基础数据并希望用 LLM 生成字段进行增强时使用：

```python
from dataflow.utils.storage import GeneratorDataSource, FileCacheStorage
from dataflow.serving.agent.cli_openclaw_serving import CLIOpenClawServing

# 定义基础数据生成器
def task_generator():
    """生成基础任务数据"""
    for i in range(1000):
        yield {
            "index": i,
            "scene": "搜索" if i % 2 == 0 else "数据分析",
            "keywords": "特斯拉" if i % 2 == 0 else "财务数据"
        }

# 创建带 LLM 增强的数据源
source = GeneratorDataSource(
    generator_fn=task_generator,
    total_rows=1000,
    name="enhanced_tasks",
    serving=CLIOpenClawServing(agent_id="main"),
    prompt_templates={
        "question": "基于场景 {scene} 和关键词 {keywords}，生成一个真实的技能使用问题。返回 JSON: {{\"question\": \"...\"}}",
        "target_skills": "基于场景 {scene}，选择 2-3 个适合的技能。返回 JSON: {{\"target_skills\": [...]}}",
    },
    fields_from_base=["index", "scene", "keywords"],
)

# 读取数据（LLM 字段实时生成）
for row in source.read(chunk_size=32):
    print(row)  # 包含：index, scene, keywords, question, target_skills
```

#### LLMGeneratorDataSource - 纯 LLM 生成

当您希望 LLM 从头生成所有数据时使用：

```python
from dataflow.utils.storage import LLMGeneratorDataSource

# 纯 LLM 生成 - 无需基础数据
source = LLMGeneratorDataSource(
    serving=CLIOpenClawServing(agent_id="main"),
    prompts={
        "question": "生成一个真实的 OpenClaw 技能使用问题。返回 JSON: {{\"question\": \"...\"}}",
        "target_skills": "为这个问题选择 2-3 个合适的技能。返回 JSON: {{\"target_skills\": [...]}}",
        "difficulty": "评估问题难度（1-5 分）。返回 JSON: {{\"difficulty\": 3}}",
    },
    num_rows=10000,
    batch_size=32,
    name="llm_generated_tasks",
)

# 读取生成的数据
for row in source.read(chunk_size=32):
    print(row)  # 包含：question, target_skills, difficulty
```

#### 使用 create_data_source 工厂函数

```python
from dataflow.utils.storage import create_data_source

# 生成器数据源
source = create_data_source(
    ["enhanced_tasks"],
    source_type="generator",
    generator_fn=task_generator,
    total_rows=1000,
    serving=CLIOpenClawServing(agent_id="main"),
    prompt_templates={
        "question": "基于场景 {scene} 生成问题",
    },
    fields_from_base=["index", "scene"],
)

# LLM 生成器数据源
source = create_data_source(
    ["llm_tasks"],
    source_type="llm_generator",
    serving=CLIOpenClawServing(agent_id="main"),
    prompts={
        "question": "生成一个技能使用问题",
    },
    num_rows=5000,
)
```

### 3. 高级进阶：大规模 S3 任务与断点续传

```python
from dataflow.pipeline import PartitionPipelineParallelRun
from dataflow.utils.storage import S3DataSource, S3Storage, S3CacheStorage

# 配置大规模 S3 任务并开启断点续传
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="YOUR_AK", sk="YOUR_SK",
    s3_paths=["s3://my-bucket/massive-dataset/"],
)

storage = S3Storage(
    data_source=source,
    id_key="task_id",
    cache_path="./local_cache",
    cache_type="jsonl"
)

# 通过 CacheStorage 实现断点续传
progress_storage = S3CacheStorage(
    endpoint="https://s3.example.com",
    ak="YOUR_AK", sk="YOUR_SK",
    cache_file="s3://my-bucket/checkpoints/pipeline_v1.json"
)

pipeline = PartitionPipelineParallelRun(
    steps=[...],
    data_source=source,
    storage=storage,
    cache_storage=progress_storage,
    partitions=1000, # 支持数千个分片
    max_parallelism=32
)

# 运行并自动恢复中断的任务
pipeline.run(resume_from_last=True)
```

---

## 📂 项目结构

```text
dataflow/
├── core/               # 核心引擎、注册表与基础抽象
├── operators/          # 丰富的内置算子库
│   ├── core_text/      # 文本处理、清洗与提取
│   ├── core_vision/    # VLM 与图像推理
│   ├── code/           # 代码合成与执行
│   ├── reasoning/      # 数学与逻辑推理
│   └── ...             # 专项领域 (RAG, PDF2VQA 等)
├── pipeline/           # 流水线编排与执行逻辑
├── serving/            # LLM/VLM 服务集成 (vLLM, OpenAI 等)
├── utils/              # 存储、注册表及各类工具函数
└── ...
```

## 🤝 Contributing

`ya-dataflow` 是一个不断进化的生态系统。我们热忱欢迎社区的贡献，以共同扩展算子库并提升性能。请访问主仓库查看贡献指南。

[GitHub Repository](https://github.com/NgZing/YetAnotherDataFlow)

## 📄 License

本项目采用 **Apache-2.0** 协议开源。
