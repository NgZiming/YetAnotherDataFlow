# ya-dataflow

**ya-dataflow** is a modern, data-centric AI framework designed to orchestrate complex, large-scale workflows for Large Language Models (LLMs) and Vision-Language Models (VLMs). 

It provides a unified, modular, and highly scalable architecture to manage the entire lifecycle of AI data processing—from raw data ingestion and sophisticated multi-modal reasoning to high-throughput trajectory synthesis and large-scale evaluation.

**Latest Version**: v1.0.15 (2026-05-15)

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

### 🎥 Multimodal & VLM Support
Native support for Vision-Language Models with seamless image encoding, base64 transmission, and multimodal message handling. Process text, images, and their combinations in a unified pipeline.

### 🧠 User Simulator with Dynamic Personas
Advanced user simulation capabilities with evidence-driven cognitive architecture:
- **Perception Stage**: Extract structured evidence from files and agent trajectories
- **Understanding Stage**: Audit progress, identify milestones, assess task state
- **Decision Stage**: Generate strategic dialogue responses with dynamic user personas
- **Emotional Modeling**: Natural emotional tone tracking (satisfied/dissatisfied/confused/urgent/neutral)

---

## 🚀 What's New in v1.0.15

### 🔄 Major Architecture Simplification
- **Complete Async Removal**: Simplified codebase by removing all `async/await` patterns from User Simulator module
- **Thread-Based Concurrency**: Replaced `asyncio.gather()` with `ThreadPoolExecutor` for better I/O parallelism
- **Simplified Debugging**: Synchronous programming model makes testing and debugging significantly easier

### 🤖 Enhanced LLM Client
- **Multimodal Input Support**: Native text + image input handling
  ```python
  prompt = [
      {"role": "user", "type": "text", "text": "Describe this image"},
      {"role": "user", "type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]
  ```
- **Embedding Support**: New `generate_embedding()` method for vector representations
- **Unified Architecture**: Consolidated LLM client implementations with `LLMClientAdapter`

### 🎥 VLM Infrastructure
- **Vision Language Model Serving**: Full VLM API support with multimodal message handling
- **Image Encoding**: Built-in base64 encoding and transmission
- **Streamlined Design**: 75% code reduction through refactoring

---

## 🚀 Why ya-dataflow? (Advanced Features)

### 💎 Enterprise-Grade Data Management
- **Cloud-Native Storage**: Native, seamless integration with **S3** and other cloud storage providers
- **Read-Write Separation**: Decoupled `DataSource` and `Storage` layers for maximum flexibility
- **Intelligent Caching**: Advanced-level caching mechanisms (`CacheStorage`) to optimize I/O
- **Checkpointing & Resumption**: Built-in support for checkpointing—resume interrupted jobs exactly where they left off

### 💎 Production-Ready Reliability
- **Fine-Grained Parallelism**: Partition-level parallelism scales throughput by splitting datasets into granular work units
- **Structured Output Validation**: Native JSON schema validation with automatic filtering of invalid samples
- **Error Recovery**: Robust error handling with automatic retry and fallback mechanisms

### 💎 Seamless Agentic Connectivity
- **OpenClaw Powered**: Use `CLIOpenClawServing` to bridge agentic reasoning with data pipelines
- **Nanobot Ready**: Integrated with `NanobotServing` for lightweight, high-performance serving
- **CLI Integration**: Direct CLI support for agent invocation and orchestration

### 💎 Multimodal Excellence
- **Text + Image Processing**: Unified handling of text and visual content
- **Base64 Encoding**: Automatic image encoding for API transmission
- **OpenAI-Compatible Format**: Standard multimodal message format support

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

# For User Simulator and agentic workflows
pip install ya-dataflow[agent]
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

### 3. Multimodal Processing with VLM (v1.0.15+)

Process text and images together with native multimodal support:

```python
from dataflow.serving.llm_client import LLMClientAdapter
from dataflow.utils.storage import FileDataSource, FileStorage

# Initialize multimodal client
client = LLMClientAdapter(
    api_url="http://localhost:8000/v1",
    client_params={"model": "/data/share/models/Qwen-VL/"}
)

# Multimodal prompt with image
prompt = [
    {"role": "user", "type": "text", "text": "Describe what you see in this image"},
    {"role": "user", "type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."}}
]

response = client.generate(prompt)
print(response)
```

### 4. User Simulator with Dynamic Personas (v1.0.15+)

Simulate realistic user interactions with evidence-driven cognitive architecture:

```python
from dataflow.serving.agent.user_v2.simulator import UserSimulator
from dataflow.serving.llm_client import LLMClientAdapter

# Initialize simulator
llm_client = LLMClientAdapter(api_url="http://localhost:8000/v1")
simulator = UserSimulator(llm_client=llm_client)

# Run simulation
raw_data = {
    "file_contents": {"config.py": "...", "main.py": "..."},
    "agent_outputs": ["Agent: I found the issue...", "Agent: Here's the fix..."],
    "feedbacks": ["This doesn't work", "Can you explain?"]
}

global_context = {
    "question": "How do I fix the configuration error?",
    "dialogue_scripts": [...]  # Optional: persona scripts per stage
}

result = simulator.run(raw_data, global_context)
print(result["final_response"])
# Output: Natural user response with emotional tone and intent
```

### 5. Generator Data Sources (v1.0.8+)

Generate data on-the-fly without relying on pre-existing files.

#### GeneratorDataSource - Base Data + LLM Enhancement

Use when you have base data and want to enhance it with LLM-generated fields:

```python
from dataflow.utils.storage import GeneratorDataSource, FileCacheStorage
from dataflow.serving.agent.cli_openclaw_serving import CLIOpenClawServing

# Define your base data generator
def task_generator():
    """Yield base task data"""
    for i in range(1000):
        yield {
            "index": i,
            "scene": "search" if i % 2 == 0 else "analysis",
            "keywords": "特斯拉" if i % 2 == 0 else "财务数据"
        }

# Create data source with LLM enhancement
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

# Read data (LLM fields are generated on-the-fly)
for row in source.read(chunk_size=32):
    print(row)  # Contains: index, scene, keywords, question, target_skills
```

#### LLMGeneratorDataSource - Pure LLM Generation

Use when you want LLM to generate all data from scratch:

```python
from dataflow.utils.storage import LLMGeneratorDataSource

# Pure LLM generation - no base data needed
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

# Read generated data
for row in source.read(chunk_size=32):
    print(row)  # Contains: question, target_skills, difficulty
```

### 6. High-Scale S3 Pipeline with Resumption

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
    partitions=1000,  # Scale to thousands of partitions
    max_parallelism=32
)

# Run with automatic resumption
pipeline.run(resume_from_last=True)
```

### 7. Structured Output with JSON Schema Validation (v1.0.14+)

Enforce strict schema validation at the LLM level:

```python
from dataflow.core.agentic import StepSchema, UserStep
from dataflow.serving.llm_client import LLMClientAdapter

# Define JSON schema for structured output
json_schema = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "target_skills": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 3
        },
        "difficulty": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5
        }
    },
    "required": ["question", "target_skills", "difficulty"]
}

# Create step with schema validation
step = UserStep(
    name="TaskGenerator",
    schema=StepSchema(
        input_keys=["scene", "keywords"],
        output_key="task",
        prompt_template="Generate a task based on scene={scene} and keywords={keywords}",
        json_schema=json_schema  # Enforce schema at LLM level
    ),
    llm_config={"temperature": 0.7}
)

# Execute with automatic validation
result = step.execute(data_pool, global_context, llm_client)
# result["json_resp"] is guaranteed to match the schema
```

---

## 📂 Project Structure

```text
dataflow/
├── core/               # Core engine, registry, and base abstractions
│   ├── agentic/        # User simulator interfaces (LLMClientABC, UserSimulatorABC)
│   └── ...
├── operators/          # Extensive library of built-in operators
│   ├── core_text/      # Text processing, cleaning, and extraction
│   ├── core_vision/    # VLM and image reasoning
│   ├── code/           # Code synthesis and execution
│   ├── reasoning/      # Math and logical reasoning
│   └── ...             # Specialized domains (RAG, PDF2VQA, etc.)
├── pipeline/           # Pipeline orchestration and execution logic
├── serving/            # LLM/VLM serving integrations
│   ├── llm_client.py   # Unified LLM client with multimodal support
│   ├── agent/          # User simulator (v1 & v2)
│   └── api_*_serving_request.py  # LLM/VLM API serving
├── utils/              # Storage, registry, and utility helpers
│   └── storage.py      # DataSource, Storage, CacheStorage, Generators
└── ...
```

---

## 📊 Version History

| Version | Date | Key Features |
|---------|------|--------------|
| **1.0.15** | 2026-05-15 | Async removal, Multimodal/VLM support, Embedding API |
| **1.0.14** | 2026-05-12 | JSON Schema validation, UserSimulator V2 enhancements |
| **1.0.13** | 2026-05-11 | Operator improvements, Performance optimizations |
| **1.0.12** | 2026-05-08 | Checkpointing, S3 integration |
| **1.0.11** | 2026-05-04 | Generator data sources, LLM synthesis |
| **1.0.10** | 2026-04-29 | Partition parallelism, Scalability |
| **1.0.9** | 2026-04-22 | RAG operators, Retrieval |
| **1.0.8** | 2026-04-16 | GeneratorDataSource, Dynamic data |
| **1.0.7** | 2026-04-15 | Code synthesis, Execution |
| **1.0.6** | 2026-04-14 | Vision support, PDF2VQA |
| **1.0.5** | 2026-04-13 | Initial release |

---

## 🤝 Contributing

`ya-dataflow` is an evolving ecosystem. We welcome contributions from the community to expand its operator library and performance capabilities. Please visit the main repository for contribution guidelines.

[GitHub Repository](https://github.com/NgZing/YetAnotherDataFlow)

---

## 📄 License

This project is licensed under the **Apache-2.0** license.

---

## 📚 Additional Resources

- [CHANGELOG.md](CHANGELOG.md) - Complete version history and change details
- [User Simulator Guide](docs/user_simulator.md) - Deep dive into agentic simulation
- [VLM Integration Guide](docs/vlm_integration.md) - Multimodal processing patterns
- [Pipeline Best Practices](docs/pipeline_patterns.md) - Production deployment tips

---

**Built with ❤️ for the data-centric AI community**
