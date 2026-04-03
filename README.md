DataFlow is an excellent tool, but the project was launched too quickly and I couldn't wait for it to implement my requirements.

After making my changes, I found the codebase had drifted too far from v1.0.8 to merge back, so I created a separate repository.

main differences:
1. S3 support
2. Partitioned / Parallel run / Dependency Analytics
3. read/write Splitting
4. openclaw serving
5. **Docker Image Support** - Build DataFlow image with OpenClaw

Welcome to use it.

## 🐳 Docker Image Build

This project provides a Dockerfile to build a DataFlow image with OpenClaw:

```bash
# Build image
docker build -t dataflow:latest .

# Run container
docker run -it --rm dataflow:latest
```

**Image Features:**
- Python 3.12 + Miniconda environment management
- Node.js 24 + OpenClaw CLI
- DataFlow core code and operators
- Optimized `.dockerignore` to exclude non-runtime files

**Usage Example:**

```bash
# After entering the container, use dataflow command directly
dataflow -v

# Run Pipeline
python my_pipeline.py
```

See [Dockerfile](Dockerfile) and [.dockerignore](.dockerignore) for detailed configuration.

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
{"id": 1, "raw_content": "Hello, world!", "translation": "你好,世界!"}
{"id": 2, "raw_content": "DataFlow is awesome!", "translation": "DataFlow 太棒了!"}
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

## 7. New Operators Tutorial (v1.0.3)

### 7.1 JsonParseFilter - JSON Parsing and Validation

Parse JSON strings returned by LLM and validate fields.

```python
from dataflow.pipeline import PipelineABC
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage
from dataflow.operators.core import JsonParseFilter


class JsonParsePipeline(PipelineABC):
    def __init__(self):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        self.data_source = FileDataSource(paths=["./input.jsonl"], format_type="jsonl")
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl",
        )

        # Create JSON parser operator
        self.json_parser = JsonParseFilter(
            required_fields=["filename", "format"],  # Required fields
            field_types={"filename": "str", "rows": "int"},  # Type checking
            field_ranges={"rows": (1, 1000)},  # Range validation
        )

    def forward(self):
        # Parse JSON string column, store result in new column
        self.json_parser.run(
            self.storage.step(),
            input_key="llm_output",  # Column with JSON strings
            output_key="parsed_data",  # Parsed dict stored here
        )
```

### 7.2 NestExtractOperator - Nested JSON Extraction

Extract fields from nested JSON structures to flat columns.

```python
from dataflow.operators.core import NestExtractOperator


class ExtractPipeline(PipelineABC):
    def __init__(self):
        # ... initialize Storage ...
        # Create nested extraction operator
        extract_op = NestExtractOperator(
            # Input column (with JSON)
            input_data_name_key="user_json.user_name",
            input_data_age_key="user_json.user_age",
            input_tags_name_key="user_json.user_tags",
            output_data_name_key="user_name",  # Extract from user_json.name
            output_data_age_key="user_age",  # Extract from user_json.age
            output_tags_name_key="user_tags",  # Extract from user_json.tags
        )

        # Support complex paths: dot notation and array indexing
        extract_op_with_path = NestExtractOperator(
            input_user_name_key="data.name",
            input_user_email_key="data.email",
            input_first_tag_key="data.tags[0]",
            input_nested_value_key="data.nested",
            output_user_name_key="name",  # data.name
            output_user_email_key="email",  # data.email
            output_first_tag_key="tags",  # data.tags[0]
            output_nested_value_key="nested",  # data.nested.value
        )
        pass

    def forward(self):
        extract_op.run(
            self.storage.step(),
        )
```

### 7.3 FileContextGenerator + FormatStrPromptedAgenticGenerator - Binary File Generation

Generate test binary files (tables/documents/PPT/code, etc.).

```python
from dataflow.pipeline import PipelineABC
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage
from dataflow.operators.agentic import FileContextGenerator
from dataflow.operators.agentic import FormatStrPromptedAgenticGenerator
from dataflow.serving import CLIOpenClawServing
from dataflow.prompts.core_text import FormatStrPrompt


class BinaryFileGenerationPipeline(PipelineABC):
    def __init__(self):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        self.data_source = FileDataSource(paths=["./tasks.jsonl"], format_type="jsonl")
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl",
        )

        # OpenClaw CLI Serving
        self.llm = CLIOpenClawServing(
            agent_id="main",
            model="custom/Qwen3.5-122B-A10B",
            max_workers=4,
        )

        # File content generation operator
        self.file_generator = FileContextGenerator(llm_serving=self.llm)

        # Template-based generation operator
        self.content_generator = FormatStrPromptedAgenticGenerator(
            llm_serving=self.llm,
            prompt_template=FormatStrPrompt,
            system_prompt="You are a helpful assistant.",
        )

    def forward(self):
        # Step 0: Generate file content based on tasks
        self.file_generator.run(
            self.storage.step(),
            input_files_key="file_paths",  # File paths column
            input_question_key="task_description",  # Task description column
            output_key="file_contents",  # Output: {filename: content_data}
        )

        # Step 1: Use file content to generate additional data
        self.content_generator.run(
            self.storage.step(),
            input_files_data_key="file_contents",  # Pass file content data
            input_task_key="task_description",
            output_key="additional_content",
        )
```

**Supported File Formats:**

| Category | Formats | Description |
|----------|---------|-------------|
| Tables | CSV, XLSX, XLS | Spreadsheet data |
| Documents | PDF, DOCX, DOC, MD | Reports, documents |
| Presentations | PPTX, PPT | Slide decks |
| Structured | JSON, XML, HTML, YAML, YML | Config files, data exchange |
| Text | TXT, LOG | Plain text, logs |
| Code | PY, JS, TS | Python/JavaScript/TypeScript |

### 7.4 Complete Example: Generate Test Dataset

```python
# tasks.jsonl content example:
# {"id": 1, "file_paths": ["/workspace/sales_data.xlsx"], "task_description": "Generate Q1 2026 sales data"}
# {"id": 2, "file_paths": ["/workspace/report.pdf"], "task_description": "Generate project progress report"}

from dataflow.pipeline import PipelineABC
from dataflow.utils.storage import FileStorage, FileDataSource, FileCacheStorage
from dataflow.operators.agentic import FileContextGenerator
from dataflow.serving import CLIOpenClawServing


class TestDataGenerationPipeline(PipelineABC):
    def __init__(self):
        progress_storage = FileCacheStorage(cache_path="./cache")
        super().__init__(progress_storage)

        self.data_source = FileDataSource(paths=["./tasks.jsonl"], format_type="jsonl")
        self.storage = FileStorage(
            data_source=self.data_source,
            id_key="id",
            cache_path="./cache",
            cache_type="jsonl",
        )

        self.llm = CLIOpenClawServing(
            agent_id="main",
            model="custom/Qwen3.5-122B-A10B",
            max_workers=4,
        )

        self.file_generator = FileContextGenerator(llm_serving=self.llm)

    def forward(self):
        self.file_generator.run(
            self.storage.step(),
            input_files_key="file_paths",
            input_question_key="task_description",
            output_key="file_contents",
        )


if __name__ == "__main__":
    pipeline = TestDataGenerationPipeline()
    pipeline.compile()
    pipeline.forward()
    print("✅ Test file generation complete!")
```

---

## Changelog

### [1.0.4] - 2026-04-03

#### Added

- **Pipeline Partition Skip Optimization**
  - `Pipeline.compile()`: Check `total_shards` in progress, skip `split_input()` if already partitioned
  - Added `is_partitioned` property to `PartitionableStorage` interface
  - Support skipping completed partitions on task restart, avoiding reprocessing

- **Storage Interface Optimization**
  - Removed `batch_size` property (dynamically calculated during partitioning)
  - `get_keys()` reads field names from DataSource

#### Changed

- **Docker Image Optimization**
  - Code copy path changed to `/opt/dataflow`
  - Updated `.dockerignore` to exclude non-runtime files
    - `dataflow/example/` - Example data
    - `dataflow/cli_funcs/` - CLI features
    - `dataflow/webui/` - Web UI
    - `static/` - Static assets

- **Local Installation Support**
  - Dockerfile now installs from local folder instead of remote git
  - Removed remote git URL containing sensitive information

#### Removed

- **BatchedPipeline Related Code**
  - Deleted `BatchedPipelineABC`, `StreamBatchedPipelineABC` classes
  - Deleted `BatchedFileStorage`, `StreamBatchedFileStorage` classes
  - Deleted test files `test/test_batched_pipeline.py`, `test/test_batched_stream_pipeline.py`
  - Deleted template file `my_pipeline.py`

#### Fixed

- **Pipeline Progress Initialization**
  - `progress["partitions"]` list length changed to `self._partitions`
  - `_build_operator_nodes_graph()` moved before progress creation

- **Pipeline Class Name Retrieval**
  - `pipeline_class` changed to `type(self).__bases__[0].__name__`
  - Ensures base class name is retrieved (`PipelineABC` or `PartitionPipelineParallelRun`)

- **Dependency Fixes**
  - `requirements.txt` added dependencies

---

### [1.0.3] - 2026-04-02

#### Added

- **Core Operators**
  - `JsonParseFilter`: JSON parsing and validation operator with type checking, regex matching, range validation
  - `NestExtractOperator`: Nested JSON extraction with dot notation (`user.address.city`) and array indexing (`items[0].name`)
  - `FormatStrPromptedAgenticGenerator`: Template-based Agent generation with file content data support
  - `FileContextGenerator`: File content synthesis for tables/documents/PPT/code

- **Binary File Generation System**
  - `generate_binary_files.py`: Support for 11 formats (CSV/XLSX/PDF/DOCX/PPTX/JSON/XML/HTML/YAML/TXT/Py/JS/TS)
  - `CLIOpenClawServing` enhancement: Inject binary file content data before LLM calls
  - 5 Prompt templates: table/document/presentation/structured/text/code

- **Dependencies**
  - Added `openpyxl`, `python-pptx`, `reportlab`, `docx`

#### Fixed

- File validation and path handling
- Pipeline input key checking

### [1.0.2] - 2026-04-01

#### Fixed

- **Pipeline input key checking**
  - `PipelineABC._check_input_keys`: Empty `input_key_first_part` check
  - `PartitionPipelineParallelRun`: Skip `key_para_name` starting with `.`

- **OpenClaw CLI Serving timeout handling**
  - Timeout from hardcoded 30s to use passed `timeout` parameter
  - Timeout exception from returning empty string to throwing exception

- **Storage schema includes id_key**
  - `FileStorage.get_schema` and `S3Storage.get_schema` return schema with `id_key`

- **File handle leak fix**
  - `FileStorage._load_data_for_pruning`: try-finally to ensure file closure

### [1.0.1] - 2026-03-31

#### Added

- **IdSynthesizer abstract class**
  - `IdSynthesizer` base class for auto-generating missing `id_key`
  - `UuidIdSynthesizer` (default) and `CounterIdSynthesizer`

- **OpenClaw CLI Serving refactoring**
  - Use pre-created worker agents
  - Execute `/new` before each request

- **Pipeline parallel check optimization**
  - `_check_completed_workloads` to multi-threaded parallel check

#### Changed

- **Session file wait enhancement**
  - `load_session` throws exception instead of returning `None`
  - `_resolve_transcript_path` timeout from 15s to 60s

- **Worker agent registration wait**
  - Poll for registration success after creating worker agent

### [1.0.0] - 2026-03-30

#### Added

- **Storage module architecture refactoring**
  - Refactored into 6 modular files
  - Data plane/Control plane separation
  - DataSource abstract class: Local, S3, HuggingFace, ModelScope

- **Smart row count estimation**
  - Parquet O(1) metadata reading
  - S3 Parquet footer-only download (~64KB)

- **PartitionPipelineParallelRun** - Large-scale parallel processing

- **Data format support**
  - Parquet, Pickle, JSON

- **S3 performance optimization**
  - S3 Range requests for Parquet footer-only download

#### Changed

- **Pipeline interface simplification**
  - `storage.step()` for automatic partition and step management
  - Automatic partitioning and dependency management

#### Documentation

- Added `TUTORIAL.md` (Chinese) and `TUTORIAL-en.md` (English)
- Updated `README.md` and `README-zh.md`

---
