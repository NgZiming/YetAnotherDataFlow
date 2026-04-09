# ya-dataflow

**ya-dataflow** is a modern, data-centric AI framework designed to orchestrate complex, large-scale workflows for Large Language Models (LLMs) and Vision-Language Models (VLMs). 

It provides a unified, modular, and highly scalable architecture to manage the entire lifecycle of AI data processing—from raw data ingestion and sophisticated multi-modal reasoning to high-throughput trajectory synthesis and large-scale evaluation.

---

## 🌟 Core Philosophy: Data-Centric AI

In the era of LLMs, the bottleneck is no longer just model capacity, but the quality and scale of the data pipelines that feed them. **ya-dataflow** is built to address this by treating data as a first-class citizen, providing a robust engine to transform raw, unstructured data into high-quality, structured datasets for training, fine-tuning, and evaluation.

---

## ✨ Key Capabilities

### 🏗️ Modular Operator Architecture
At the heart of `ya-dataflow` is a highly extensible operator-based design. Every task—whether it's a simple text cleaning step, a complex RAG retrieval, or a sophisticated VLM reasoning process—is implemented as a standardized `Operator`. This allows for seamless composition of complex pipelines.

### 🚀 Scalable Pipeline Execution
Designed for massive workloads, `ya-dataflow` supports advanced execution strategies like `PartitionPipelineParallelRun`. It enables high-throughput, parallel processing across distributed environments, making it capable of handling billions of tokens or millions of multi-modal assets.

### 🧪 High-Fidelity Synthetic Data Generation
Leveraging a powerful binary and structured file generation system, `ya-dataflow` can synthesize diverse datasets—including JSON, XML, Markdown, HTML, and complex binary formats like PDF, XLSX, and PPTX—to create robust testing and training environments.

### 🤖 Agentic Ecosystem Integration
Deeply integrated with the **OpenClaw** and **Nanobot** ecosystems. Through `CLIOpenClawServing` and `NanobotServing`, your data pipelines become accessible intelligence that can be dynamically invoked and orchestrated by autonomous agents.

---

## 🚀 Why ya-dataflow? (Advanced Features)

While many frameworks handle simple tasks, `ya-dataflow` is engineered for **production-grade, massive-scale AI data engineering**.

### 💎 Enterprise-Grade Data Management
- **Cloud-Native Storage**: Native, seamless integration with **S3** and other cloud storage providers.
- **Read-Write Separation**: Decoupled `DataSource` and `Storage` layers for maximum flexibility and control.
- **Intelligent Caching**: Advanced-level caching mechanisms (`CacheStorage`) to optimize I/O and accelerate repetitive workloads.

### 💎 Production-Ready Reliability
- **Checkpointing & Resumption**: Built-in support for **checkpointing**. If a massive job (running for days) is interrupted, it can resume exactly from where it left off.
- **Fine-Grained Parallelism**: Beyond simple task parallelism, our **Partition-level parallelism** allows you to scale throughput by splitting datasets into granular work units.

### 💎 Seamless Agentic Connectivity
- **OpenClaw Powered**: Use `CLIOpenClawServing` to bridge the gap between agentic reasoning and heavy-duty data pipelines.
- **Nanobot Ready**: Integrated with `NanobotServing` for lightweight, high-performance serving within the Nanobot SDK ecosystem.

---

## 🚀 Getting Started

### 1. Installation

Install the core framework:
```bash
pip install ya-dataflow
```

Install with specialized capabilities via extras:
```bash
# For RAG workflows
pip install ya-dataflow[rag]

# For Multimodal (VLM) and PDF processing
pip install ya-dataflow[pdf2vqa]

# For LLM serving and high-performance evaluation
pip install ya-dataflow[vllm,eval]

# For Code and Math reasoning tasks
pip install ya-dataflow[code,reasoning]
```

### 2. Basic Usage (Python API)

In production, you define a pipeline by inheriting from `PartitionPipelineParallelRun` and implementing the `forward` method to orchestrate your operators.

```python
from dataflow.pipeline import PartitionPipelineParallelRun
from dataflow.operators.core_text import TextCleaningOperator
from dataflow.utils.storage import FileDataSource, FileStorage, FileCacheStorage
from dataflow.serving.api_llm_serving_request import APILLMServing_request

class MyProductionPipeline(PartitionPipelineParallelRun):
    def __init__(self, source: FileDataSource, storage: FileStorage, llm_serving: APILLMServing_request):
        # 1. Initialize CacheStorage (Crucial: cannot be None)
        cache_storage = FileCacheStorage(cache_path="./cache")
        
        # 2. Initialize base class with cache_storage and explicit partitions
        super().__init__(cache_storage=cache_storage, partitions=10)
        
        self.storage = storage
        self.llm_serving = llm_serving
        
        # Define Operators
        self.clean_op = TextCleaningOperator(self.llm_serving)
        self.refine_op = SomeRefineOperator(self.llm_serving)

    def forward(self):
        # Step 1: Clean the raw text
        # .step() retrieves the current partition's data
        self.clean_op.run(
            self.storage.step(),
            input_key="raw_text",
            output_key="cleaned_text"
        )

        # Step 2: Refine based on cleaned text (Dependency: cleaned_text)
        self.refine_op.run(
            self.storage.step(),
            input_key="raw_text",
            output_key="final_result",
            input_prev_1="cleaned_text" 
        )

# Usage
source = FileDataSource(paths=["./input.jsonl"])
storage = FileStorage(data_source=source, id_key="id", cache_path="./cache")
llm = APILLMServing_request(api_url="...", model_name="...")

pipeline = MyProductionPipeline(source, storage, llm)
pipeline.compile()
pipeline.run()
```

### 3. Advanced: High-Scale S3 Pipeline with Resumption

```python
from dataflow.pipeline import PartitionPipelineParallelRun
from dataflow.utils.storage import S3DataSource, S3Storage, S3CacheStorage

# Configure massive S3-based workflow with checkpointing
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

# Enable checkpointing via CacheStorage
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
    partitions=1000, # Scale to thousands of partitions
    max_parallelism=32
)

# Run with automatic resumption
pipeline.run(resume_from_last=True)
```

---

## 📂 Project Structure

```text
dataflow/
├── core/               # Core engine, registry, and base abstractions
├── operators/          # Extensive library of built-in operators
│   ├── core_text/      # Text processing, cleaning, and extraction
│   ├── core_vision/    # VLM and image reasoning
│   ├── code/           # Code synthesis and execution
│   ├── reasoning/      # Math and logical reasoning
│   └── ...             # Specialized domains (RAG, PDF2VQA, etc.)
├── pipeline/           # Pipeline orchestration and execution logic
├── serving/            # LLM/VLM serving integrations (vLLM, OpenAI, etc.)
├── utils/              # Storage, registry, and utility helpers
└── ...
```

## 🤝 Contributing

`ya-dataflow` is an evolving ecosystem. We welcome contributions from the community to expand its operator library and performance capabilities. Please visit the main repository for contribution guidelines.

[GitHub Repository](https://github.com/NgZing/YetAnotherDataFlow)

## 📄 License

This project is licensed under the **Apache-2.0** license.
