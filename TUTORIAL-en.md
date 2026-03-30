# DataFlow Pipeline Tutorial for Beginners

> This tutorial will guide you from zero to building a complete AI data processing Pipeline with DataFlow.

## 📚 Table of Contents

1. [Environment Setup](#1-environment-setup)
2. [Your First Pipeline: Text Translation](#2-your-first-pipelinetext-translation)
3. [Advanced: Multi-Step Pipeline](#3-advanced-multi-step-pipeline)
4. [New Features: Row Estimation & Partitioning](#4-new-features-row-estimation--partitioning)
5. [S3 Data Source Support](#5-s3-data-source-support)
6. [FAQ](#6-faq)

---

## 1. Environment Setup

### 1.1 Install DataFlow

```bash
# Create virtual environment
conda create -n dataflow python=3.10
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
from dataflow.pipeline import BatchedPipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import BatchedFileStorage

class TranslatePipeline(BatchedPipelineABC):
    def __init__(self):
        super().__init__()
        
        # 1. Create Storage
        self.storage = BatchedFileStorage(
            first_entry_file_name="./input.jsonl",
            cache_path="./cache",
            file_name_prefix="translate",
            cache_type="jsonl",
        )
        
        # 2. Create LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )
        
        # 3. Create translation operator
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please translate the following to Chinese:",
        )
    
    def forward(self):
        # Execute translation
        self.translate_op.run(
            self.storage.step(),
            input_key='raw_content',
            output_key='translation'
        )

if __name__ == "__main__":
    # Create Pipeline
    pipeline = TranslatePipeline()
    
    # Compile (initialize Storage)
    pipeline.compile()
    
    # Execute
    pipeline.forward(batch_size=10, resume_from_last=True)
    
    print("✅ Translation complete! Results saved to ./cache directory")
```

### 2.3 Run Pipeline

```bash
python translate_pipeline.py
```

### 2.4 View Results

```bash
cat cache/translate_last.jsonl
```

Output example:
```json
{"id": 1, "raw_content": "Hello, world!", "translation": "你好，世界！"}
{"id": 2, "raw_content": "DataFlow is awesome!", "translation": "DataFlow 太棒了！"}
{"id": 3, "raw_content": "Machine learning is fun", "translation": "机器学习很有趣"}
```

---

## 3. Advanced: Multi-Step Pipeline

### 3.1 Scenario: Translate + Polish + Summarize

```python
from dataflow.pipeline import BatchedPipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import BatchedFileStorage

class MultiStepPipeline(BatchedPipelineABC):
    def __init__(self):
        super().__init__()
        
        self.storage = BatchedFileStorage(
            first_entry_file_name="./input.jsonl",
            cache_path="./cache",
            file_name_prefix="multistep",
            cache_type="jsonl",
        )
        
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )
        
        # Step 1: Translate
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please translate the following to Chinese:",
        )
        
        # Step 2: Polish
        self.polish_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please polish the following Chinese text:",
        )
        
        # Step 3: Summarize
        self.summary_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="Please generate a short summary:",
        )
    
    def forward(self):
        # Step 1: Translate
        self.translate_op.run(
            self.storage.step(),
            input_key='raw_content',
            output_key='translation'
        )
        
        # Step 2: Polish (depends on step 0)
        self.storage.batch_step = 1
        self.storage.operator_step = 0
        self.storage.load_partition(dependent_steps=[0])
        
        self.polish_op.run(
            self.storage.step(),
            input_key='translation',
            output_key='polished'
        )
        
        # Step 3: Summarize (depends on step 1)
        self.storage.batch_step = 2
        self.storage.operator_step = 0
        self.storage.load_partition(dependent_steps=[1])
        
        self.summary_op.run(
            self.storage.step(),
            input_key='polished',
            output_key='summary'
        )

if __name__ == "__main__":
    pipeline = MultiStepPipeline()
    pipeline.compile()
    pipeline.forward(batch_size=10, resume_from_last=True)
```

---

## 4. New Features: Row Estimation & Partitioning

### 4.1 Why Row Estimation?

When processing large datasets, traditional methods require:
1. Read all data to count rows
2. Partition based on count

**Problem**: For GB/TB scale data, reading twice is extremely inefficient!

### 4.2 New Feature: Smart Row Estimation

DataFlow now supports row estimation for multiple data sources:

```python
from dataflow.utils.storage import (
    FileDataSource,
    S3DataSource,
    HuggingFaceDataSource,
    ModelScopeDataSource,
    FileStorage,
)

# Local file - Parquet format (read metadata directly, O(1) complexity)
source = FileDataSource(
    paths=["data/large_file.parquet"],
    format_type="parquet"
)
total_rows = source.estimate_total_rows()  # Instant!

# Local file - CSV/JSONL format (sampling estimation)
source = FileDataSource(
    paths=["data/large_file.jsonl"],
    format_type="jsonl"
)
total_rows = source.estimate_total_rows()  # Sample 100 rows

# S3 file - Parquet format (download only footer, ~64KB)
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/large_file.parquet"],
    format_type="parquet"
)
total_rows = source.estimate_total_rows()  # Only 64KB downloaded!

# HuggingFace dataset (call API directly)
source = HuggingFaceDataSource(
    dataset="openai/gsm8k",
    split="train"
)
total_rows = source.estimate_total_rows()  # Returns exact count
```

### 4.3 Partitioning Optimization: Streaming

```python
# Old method (deprecated)
rows = storage.data_source.read()
total = sum(1 for _ in rows)  # ❌ Need to read all data
storage.split_input(num_partitions=10)

# New method (recommended)
storage.split_input(num_partitions=10)  # ✅ Auto-estimate + stream partition
# Internal flow:
# 1. estimate_total_rows() → estimate row count
# 2. Stream read + partition → one pass
```

### 4.4 Performance Comparison

| Data Scale | Old Method | New Method | Improvement |
|-----------|------------|------------|-------------|
| 1GB JSONL | ~30s | ~5s | 6x |
| 10GB Parquet | ~5min | ~10s | 30x |
| 100GB S3 Parquet | ~30min | ~1min | 30x |

---

## 5. S3 Data Source Support

### 5.1 Read from S3

```python
from dataflow.utils.storage import S3DataSource, S3Storage

# Create S3 data source
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="YOUR_ACCESS_KEY",
    sk="YOUR_SECRET_KEY",
    s3_paths=["s3://your-bucket/input/"],  # Supports directory
    format_type="jsonl"
)

# Create Storage
storage = S3Storage(
    data_source=source,
    cache_path="./cache",
    cache_type="jsonl"
)

# Partition and process
storage.split_input(num_partitions=10)

# Process step 0
storage.batch_step = 0
storage.operator_step = 0
df = storage.read()
print(f"Read {len(df)} rows")
```

### 5.2 S3 Parquet Optimization

For Parquet files on S3, DataFlow intelligently downloads only the footer (~64KB) to get row count:

```python
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/large.parquet"],
    format_type="parquet"
)

# Estimate rows (downloads only 64KB, not the whole file!)
total_rows = source.estimate_total_rows()
print(f"Total rows: {total_rows}")
```

---

## 6. FAQ

### Q1: How to handle超大 files (>100GB)?

```python
# Use S3DataSource + partitioning
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/huge_file.jsonl"],
    format_type="jsonl"
)

storage = S3Storage(data_source=source, cache_path="./cache")

# Split into 100 partitions
storage.split_input(num_partitions=100)

# Parallel processing
from dataflow.pipeline import PartitionPipelineParallelRun
pipeline = PartitionPipelineParallelRun(storage, partitions=100)
pipeline.compile()
pipeline.forward(max_parallelism=10)  # 10 concurrent
```

### Q2: How to resume from checkpoint?

```python
# First run
pipeline.forward(batch_size=10, resume_from_last=True)

# Resume after interruption
pipeline.forward(batch_size=10, resume_from_last=True)  # Continue from where it left off
```

### Q3: How to handle mixed file formats?

```python
from dataflow.utils.storage import FileDataSource

# Mixed formats
source = FileDataSource(
    paths=[
        "data/part1.jsonl",
        "data/part2.csv",
        "data/part3.parquet",
    ],
    format_type="jsonl"  # Unified format (data content must be consistent)
)
```

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

---

## 🎯 Next Steps

- [Official Documentation](https://OpenDCAI.github.io/DataFlow-Doc/)
- [WebUI Tutorial](#54-webui)
- [Example Code](./test/)
- [Awesome DataFlow](./awesome_dataflow.md)

---

## 📝 Changelog

### v1.1.0 (2026-03-30)
- ✨ Added `estimate_total_rows()` method for smart row estimation
- ⚡ Parquet format reads metadata directly, O(1) complexity
- 🚀 S3 Parquet downloads only footer (~64KB), huge performance boost
- 🔄 Partitioning optimization: streaming, avoid double read
- 🐛 Fixed multiple edge case bugs

---

<div align="center">

**Have questions? Check [GitHub Issues](https://github.com/OpenDCAI/DataFlow/issues) or join [Community](#11-community--support)**

</div>
