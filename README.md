DataFlow is an excellent tool, but the project was launched too quickly and I couldn't wait for it to implement my requirements.

After making my changes, I found the codebase had drifted too far from v1.0.8 to merge back, so I created a separate repository.

Welcome to use it.

# DataFlow Refactoring Summary

> AI-generated content

> This document summarizes the design differences between the current version and the official v1.0.10, suitable as supplementary reading to README.md.

---

## Core Improvements Overview

| Module     | Official v1.0.10           | Current Version                  | Core Advantage              |
| ---------- | -------------------------- | -------------------------------- | --------------------------- |
| **Storage**| Single file (1184 lines), No S3 | Modular (~5000+ lines), S3 support | Data plane/Control plane separation |
| **Pipeline**| 697 lines, No parallelism | 1225 lines, Spark-style          | Native parallel partitioning |
| **Serving**| Basic implementation       | Enhanced + OpenClaw              | Retry/Multi-modal/OpenClaw  |
| **Data Scale** | In-memory batch load | Streaming processing             | TB-level data support       |

---

## 1. Storage Module

[Reference Documentation](dataflow/utils/storage/README.md)

### 1.1 Comparison Advantages

| Feature                       | Official v1.0.10 | Current Version         |
| ----------------------------- | ---------------- | ----------------------- |
| Architecture                  | Single file, mixed responsibilities | Modular, separated responsibilities |
| Data plane/Control plane      | Not separated    | Clearly separated       |
| DataSource                    | None             | Supports S3/HF/MS/Local |
| MediaStorage/CacheStorage     | None             | Abstracted for complex storage environments |
| Partitioning                  | None             | Native support          |
| Multi-step merging            | None             | load_partition with intersection |

### 1.2 Why These Changes?

| Change                         | Reason                                                     |
| ------------------------------ | ---------------------------------------------------------- |
| DataSource streaming read      | Real-world scenarios with tens of millions of rows/TB data, avoid memory explosion |
| DataSource abstraction         | Solve complex environment read/write permissions, enterprise data storage locations and access controls are strict |
| MediaStorage/CacheStorage abstraction | Complex enterprise storage environments, need unified management of media files and cache |
| S3 support                     | Official version does not support S3 yet, need to adapt in advance |
| Data plane/Control plane separation | Personal engineering taste, improvements on original dataflow design |

---

## 2. Serving Module

### 2.1 Comparison Advantages

| Feature           | Official v1.0.10 | Current Version      |
| ----------------- | ---------------- | -------------------- |
| MediaStorage integration | None    | Supported            |
| CLI integration   | None             | CLIOpenClawServing   |
| API Key validation| Mandatory        | Optional             |

### 2.2 Why These Changes?

| Change                              | Reason                                   |
| ----------------------------------- | ---------------------------------------- |
| APIVLMServing_openai retry mechanism | Improve API call stability               |
| MediaStorage integration            | Support reading media files from Storage, unified management |
| CLIOpenClawServing addition         | Call Agent via OpenClaw CLI, support concurrency |
| API Key validation made optional    | Some scenarios don't require API Key     |
| Added max_completion_tokens         | Control generation length                |

---

## 3. Pipeline Module

### 3.1 Comparison Advantages

| Feature            | Official v1.0.10 | Current Version        |
| ------------------ | ---------------- | ---------------------- |
| Workload class     | None             | Added                  |
| Parallel partition execution | Manual handling | PartitionPipelineParallelRun |
| Dependency management | None          | Complete management    |
| execute_workload() | None             | Separate method        |
| Resume from checkpoint | Simple      | Full support           |

### 3.2 Why These Changes?

| Change                             | Reason                                                                                               |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Workload class introduction        | Lessons learned from Spark, clearer representation of workload status                                |
| PartitionPipelineParallelRun class | Enterprise environments with many models and API services, identify independent requests through dependency analysis, bypass original constraints to meet data delivery pressure |
| Complete dependency management     | Support complex multi-step Pipelines, automatic dependency handling                                   |

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

[简体中文](./README-zh.md) | English

