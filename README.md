DataFlow is an excellent tool, but the project was launched too quickly and I couldn't wait for it to implement my requirements.

After making my changes, I found the codebase had drifted too far from v1.0.8 to merge back, so I created a separate repository.

main differences：
1. S3 support
2. Partitioned / Parallel run / Dependency Analytics
3. read/write Splitting
4. openclaw serving

Welcome to use it.

# DataFlow Pipeline Tutorial for Beginners

> This tutorial will guide you from zero to building a complete AI data processing Pipeline with DataFlow.

## 📚 Table of Contents

1. [Environment Setup](#1-environment-setup)
2. [Your First Pipeline: Text Translation](#2-your-first-pipelinetext-translation)
3. [Multi-Step Pipeline](#3-multi-step-pipeline)
4. [S3 Data Source Support](#4-s3-data-source-support)
5. [FAQ](#5-faq)

---

## 1. Environment Setup

### 1.1 Install DataFlow

```bash
# Create virtual environment
conda create -n dataflow python=3.12
conda activate dataflow

# Install DataFlow
pip install open-dataflow
```

### 1.2 Verify Installation

```bash
dataflow -v
```

You should see:
```
open-dataflow codebase version: 1.0.0
```

---

## 2. Your First Pipeline: Text Translation

### 2.1 Prepare Data

Create an `input.jsonl` file:

```json
{"id": 1, "raw_content": "Hello, world!"}
{"id": 2, "raw_content": "DataFlow is awesome!"}
{"id": 3, "raw_content": "Machine learning is fun"}
```

### 2.2 Write Pipeline

Create `translate_pipeline.py`:

```python
from dataflow.pipeline import PipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage


class TranslatePipeline(PipelineABC):
    def __init__(self):
        # 1. Create progress storage (required by PipelineABC)
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # 2. Create DataSource (supports multiple paths)
        self.data_source = FileDataSource(
            paths=["./input.jsonl", "./input_dir/"],  # Mix of files and directories
            format_type="jsonl",
        )

        # 3. Create Storage (id_key is required!)
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",  # Required! Used to uniquely identify each row
            cache_path="./cache",
            cache_type="jsonl",
        )

        # 4. Create LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )

        # 5. Create translation operator
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please translate the following to Chinese:",
        )

    def forward(self):
        # Use storage.step() for automatic partition and step management
        self.translate_op.run(
            self.storage.step(),
            input_key="raw_content",
            output_key="translation",
        )


if __name__ == "__main__":
    # Create Pipeline
    pipeline = TranslatePipeline()

    # Compile (initialize)
    pipeline.compile()

    # Execute
    pipeline.forward()

    print("✅ Translation complete! Results saved to ./cache directory")
```

### 2.3 Run Pipeline

```bash
python translate_pipeline.py
```

### 2.4 View Results

```bash
cat cache/partition_1.jsonl
```

Output example:
```json
{"id": 1, "raw_content": "Hello, world!", "translation": "你好，世界！"}
{"id": 2, "raw_content": "DataFlow is awesome!", "translation": "DataFlow 太棒了！"}
{"id": 3, "raw_content": "Machine learning is fun", "translation": "机器学习很有趣"}
```

---

## 3. Multi-Step Pipeline

### 3.1 Scenario: Translate + Polish + Summarize

```python
from dataflow.pipeline import PipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage


class MultiStepPipeline(PipelineABC):
    def __init__(self):
        # 1. Create progress storage
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # 2. Create DataSource
        self.data_source = FileDataSource(
            paths=["./input.jsonl"],
            format_type="jsonl",
        )

        # 3. Create Storage
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl",
        )

        # 4. Create LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )

        # 5. Create operators
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please translate the following to Chinese:",
        )

        self.polish_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please polish the following Chinese text:",
        )

        self.summary_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please generate a short summary:",
        )

    def forward(self):
        # Step 0: Translate
        self.translate_op.run(
            self.storage.step(),
            input_key="raw_content",
            output_key="translation",
        )

        # Step 1: Polish (automatically depends on step 0)
        self.polish_op.run(
            self.storage.step(),
            input_key="translation",
            output_key="polished",
        )

        # Step 2: Summarize (automatically depends on step 1)
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

## 4. S3 Data Source Support

### 4.1 Read from S3

```python
from dataflow.utils.storage import S3DataSource, S3Storage, FileCacheStorage
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.pipeline import PipelineABC


class S3Pipeline(PipelineABC):
    def __init__(self):
        # 1. Create progress storage
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        # 2. Create S3 DataSource
        self.data_source = S3DataSource(
            endpoint="https://s3.example.com",
            ak="YOUR_ACCESS_KEY",
            sk="YOUR_SECRET_KEY",
            s3_paths=["s3://your-bucket/input/"],  # Supports directory
            format_type="jsonl"
        )

        # 3. Create S3 Storage
        self.storage = S3Storage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl"
        )

        # 4. Create LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )

        # 5. Create operator
        self.process_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please process the following:",
        )

    def forward(self):
        # Use storage.step() to process data
        self.process_op.run(
            self.storage.step(),
            input_key="raw_content",
            output_key="result",
        )
```

---

## 5. FAQ

### Q1: How to handle huge files (>100GB)?

Use `PartitionPipelineParallelRun` for parallel partition processing:

```python
from dataflow.utils.storage import S3DataSource, S3Storage, FileCacheStorage
from dataflow.pipeline import PartitionPipelineParallelRun


class LargeFilePipeline(PartitionPipelineParallelRun):
    def __init__(self, partitions: int):
        progress_storage = FileCacheStorage(cache_path="./cache")
        # Pass number of partitions
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
pipeline.forward(max_parallelism=10)  # 10 concurrent
```

### Q2: How to resume from checkpoint?

```python
# PipelineABC supports checkpoint resume
pipeline.forward(resume_from_last=True)  # Continue from where it left off
```

### Q3: Can DataSource and Storage use different formats?

**Yes!** DataSource and Storage can independently choose format and storage type:

```python
# Example: Read S3 JSONL, write local Parquet
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

        # Storage: Local Parquet
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./output",
            cache_type="parquet"  # Output format can be Parquet
        )

    def forward(self):
        pass
```

**Note**: All files in the same DataSource must be in the **same format**.

### Q4: How to customize Operator?

```python
from dataflow.operators import OperatorABC


class MyCustomOperator(OperatorABC):
    def run(self, storage, **kwargs):
        df = storage.read()

        # Custom processing logic
        df['new_column'] = df['old_column'].apply(self.process)

        storage.write(df)

    def process(self, value):
        # Your processing logic
        return value.upper()


# Usage
my_op = MyCustomOperator()
my_op.run(storage, input_key='text', output_key='upper_text')
```

### Q5: What is the purpose of `id_key`?

`id_key` is used to uniquely identify each row of data, supporting:
- **Checkpoint resume**: Skip already processed data
- **Deduplication**: Avoid duplicate processing
- **Incremental updates**: Only process new/modified data

```python
# If data doesn't have an id field, use another unique field
self.storage = FileStorage(
    data_source=self.data_source,
    id_key="url",  # Use url as unique identifier
    cache_path="./cache",
    cache_type="jsonl",
)
```

---

## 🎯 Next Steps

- [Official Documentation](https://OpenDCAI.github.io/DataFlow-Doc/)
- [WebUI Tutorial](#54-webui)
- [Example Code](./test/)
- [Awesome DataFlow](./awesome_dataflow.md)

---

<div align="center">

**Have questions? Check [GitHub Issues](https://github.com/OpenDCAI/DataFlow/issues) or join [Community](#11-community--support)**

</div>


# DataFlow Refactoring Summary

> AI-generated content

> This document summarizes the design differences between the current version and the official v1.0.10, suitable as supplementary reading to README.md.

---

## Core Improvements Overview

| Module         | Official v1.0.10                | Current Version                    | Core Advantage                      |
| -------------- | ------------------------------- | ---------------------------------- | ----------------------------------- |
| **Storage**    | Single file (1184 lines), No S3 | Modular (~5000+ lines), S3 support | Data plane/Control plane separation |
| **Pipeline**   | 697 lines, No parallelism       | 1225 lines, Spark-style            | Native parallel partitioning        |
| **Serving**    | Basic implementation            | Enhanced + OpenClaw                | Retry/Multi-modal/OpenClaw          |
| **Data Scale** | In-memory batch load            | Streaming processing               | TB-level data support               |

---

## 1. Storage Module

[Reference Documentation](dataflow/utils/storage/README.md)

### 1.1 Comparison Advantages

| Feature                   | Official v1.0.10                    | Current Version                             |
| ------------------------- | ----------------------------------- | ------------------------------------------- |
| Architecture              | Single file, mixed responsibilities | Modular, separated responsibilities         |
| Data plane/Control plane  | Not separated                       | Clearly separated                           |
| DataSource                | None                                | Supports S3/HF/MS/Local                     |
| MediaStorage/CacheStorage | None                                | Abstracted for complex storage environments |
| Partitioning              | None                                | Native support                              |
| Multi-step merging        | None                                | load_partition with intersection            |

### 1.2 Why These Changes?

| Change                                | Reason                                                                                                             |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| DataSource streaming read             | Real-world scenarios with tens of millions of rows/TB data, avoid memory explosion                                 |
| DataSource abstraction                | Solve complex environment read/write permissions, enterprise data storage locations and access controls are strict |
| MediaStorage/CacheStorage abstraction | Complex enterprise storage environments, need unified management of media files and cache                          |
| S3 support                            | Official version does not support S3 yet, need to adapt in advance                                                 |
| Data plane/Control plane separation   | Personal engineering taste, improvements on original dataflow design                                               |

---

## 2. Serving Module

### 2.1 Comparison Advantages

| Feature                  | Official v1.0.10 | Current Version    |
| ------------------------ | ---------------- | ------------------ |
| MediaStorage integration | None             | Supported          |
| CLI integration          | None             | CLIOpenClawServing |
| API Key validation       | Mandatory        | Optional           |

### 2.2 Why These Changes?

| Change                               | Reason                                                       |
| ------------------------------------ | ------------------------------------------------------------ |
| APIVLMServing_openai retry mechanism | Improve API call stability                                   |
| MediaStorage integration             | Support reading media files from Storage, unified management |
| CLIOpenClawServing addition          | Call Agent via OpenClaw CLI, support concurrency             |
| API Key validation made optional     | Some scenarios don't require API Key                         |
| Added max_completion_tokens          | Control generation length                                    |

---

## 3. Pipeline Module

### 3.1 Comparison Advantages

| Feature                      | Official v1.0.10 | Current Version              |
| ---------------------------- | ---------------- | ---------------------------- |
| Workload class               | None             | Added                        |
| Parallel partition execution | Manual handling  | PartitionPipelineParallelRun |
| Dependency management        | None             | Complete management          |
| execute_workload()           | None             | Separate method              |
| Resume from checkpoint       | Simple           | Full support                 |

### 3.2 Why These Changes?

| Change                             | Reason                                                                                                                                                                           |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Workload class introduction        | Lessons learned from Spark, clearer representation of workload status                                                                                                            |
| PartitionPipelineParallelRun class | Enterprise environments with many models and API services, identify independent requests through dependency analysis, bypass original constraints to meet data delivery pressure |
| Complete dependency management     | Support complex multi-step Pipelines, automatic dependency handling                                                                                                              |

---

## 4. Actual Results

- **Data Scale**: Supports tens of millions of rows, TB-level data
- **Memory Usage**: Streaming processing, constant memory footprint regardless of data volume
- **Parallelism**: Partition-level concurrency + operator-level concurrency, maximize parallelism
- **Resume from checkpoint**: Full support, more reliable fault recovery
- **API Stability**: Retry mechanism improves success rate
- **OpenClaw Integration**: Call Agent via CLI, support concurrency
- **S3 Support**: Adapt features not yet supported by official version

---

## 5. Compatibility Notes

### Operators Unaffected

**Existing Operator code requires no changes!** Storage module changes are completed entirely within the control plane (Pipeline) and data plane (Storage), Operator's `run()` interface remains unchanged:

```python
# Operator code unaffected
class MyOperator(OperatorABC):
    def run(self, storage: StorageABC, **kwargs):
        df = storage.read()      # Interface unchanged
        # ... processing logic ...
        storage.write(df)        # Interface unchanged
```

### Incompatible Changes (affects Pipeline authors only)
1. Storage initialization requires `data_source` instead of `first_entry_file_name`
2. API change: `get_keys_from_dataframe` → `get_keys`
3. Partitioning flow: must call `split_input()` before `read()`

### Backward Compatibility
1. `_partitions` defaults to 1, maintains backward compatibility
2. Basic read/write interfaces (`read`, `write`) remain unchanged

---

## 6. Quick Reference

### Storage Usage Example

```python
# 1. Create DataSource
source = S3DataSource(endpoint, ak, sk, s3_paths, format_type="jsonl")

# 2. Create Storage
storage = FileStorage(data_source=source, cache_path="./cache")

# 3. Partitioning (must call first)
storage.split_input(num_partitions=10)

# 4. Step 0
storage.batch_step = 0
storage.operator_step = 0
df = storage.read()  # Directly read files[0]

# 5. Steps >0
storage.operator_step = 1
storage.load_partition(dependent_steps=[0])
df = storage.read()  # Return load_partition result
```

### Pipeline Usage Example

```python
# Parallel partition execution
pipeline = PartitionPipelineParallelRun(cache_storage, partitions=10)
pipeline.compile()
pipeline.forward(max_parallelism=4)  # Concurrent execution
```

### Serving Usage Example

```python
# OpenClaw CLI integration
from dataflow.serving import create_openclaw_serving

serving = create_openclaw_serving(
    agent_id="main",
    model="custom/Qwen3.5-122B-A10B",
    max_workers=4,
)
responses = serving.generate_from_input(["Question 1", "Question 2"])
```

---
