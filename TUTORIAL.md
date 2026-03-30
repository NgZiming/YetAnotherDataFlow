# DataFlow Pipeline 新手教程

> 本教程将带你从零开始，使用 DataFlow 构建一个完整的 AI 数据处理 Pipeline。

## 📚 目录

1. [环境准备](#1-环境准备)
2. [第一个 Pipeline：文本翻译](#2-第一个-pipeline文本翻译)
3. [高级功能：多步骤 Pipeline](#3-高级功能多步骤-pipeline)
4. [新特性：行数估算与分片优化](#4-新特性行数估算与分片优化)
5. [S3 数据源支持](#5-s3-数据源支持)
6. [常见问题](#6-常见问题)

---

## 1. 环境准备

### 1.1 安装 DataFlow

```bash
# 创建虚拟环境
conda create -n dataflow python=3.10
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
from dataflow.pipeline import BatchedPipelineABC
from dataflow.operators.core_text import PromptedGenerator
from dataflow.serving import APILLMServing_request
from dataflow.utils.storage import BatchedFileStorage

class TranslatePipeline(BatchedPipelineABC):
    def __init__(self):
        super().__init__()
        
        # 1. 创建 Storage
        self.storage = BatchedFileStorage(
            first_entry_file_name="./input.jsonl",
            cache_path="./cache",
            file_name_prefix="translate",
            cache_type="jsonl",
        )
        
        # 2. 创建 LLM Serving
        self.llm = APILLMServing_request(
            api_url="http://localhost:8000/v1/chat/completions",
            model_name="qwen-7b",
            max_workers=4,
        )
        
        # 3. 创建翻译算子
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请将以下内容翻译成中文：",
        )
    
    def forward(self):
        # 执行翻译
        self.translate_op.run(
            self.storage.step(),
            input_key='raw_content',
            output_key='translation'
        )

if __name__ == "__main__":
    # 创建 Pipeline
    pipeline = TranslatePipeline()
    
    # 编译（初始化 Storage）
    pipeline.compile()
    
    # 执行
    pipeline.forward(batch_size=10, resume_from_last=True)
    
    print("✅ 翻译完成！结果保存在 ./cache 目录")
```

### 2.3 运行 Pipeline

```bash
python translate_pipeline.py
```

### 2.4 查看结果

```bash
cat cache/translate_last.jsonl
```

输出示例：
```json
{"id": 1, "raw_content": "Hello, world!", "translation": "你好，世界！"}
{"id": 2, "raw_content": "DataFlow is awesome!", "translation": "DataFlow 太棒了！"}
{"id": 3, "raw_content": "Machine learning is fun", "translation": "机器学习很有趣"}
```

---

## 3. 高级功能：多步骤 Pipeline

### 3.1 场景：翻译 + 润色 + 摘要

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
        
        # 步骤 1: 翻译
        self.translate_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请将以下内容翻译成中文：",
        )
        
        # 步骤 2: 润色
        self.polish_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请润色以下中文文本，使其更自然流畅：",
        )
        
        # 步骤 3: 摘要
        self.summary_op = PromptedGenerator(
            llm_serving=self.llm,
            system_prompt="请为以下文本生成一个简短摘要：",
        )
    
    def forward(self):
        # 步骤 1: 翻译
        self.translate_op.run(
            self.storage.step(),
            input_key='raw_content',
            output_key='translation'
        )
        
        # 步骤 2: 润色（依赖步骤 1）
        self.storage.batch_step = 1
        self.storage.operator_step = 0
        self.storage.load_partition(dependent_steps=[0])
        
        self.polish_op.run(
            self.storage.step(),
            input_key='translation',
            output_key='polished'
        )
        
        # 步骤 3: 摘要（依赖步骤 2）
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

## 4. 新特性：行数估算与分片优化

### 4.1 为什么需要行数估算？

在大数据集处理时，传统方法需要：
1. 先读取所有数据统计行数
2. 再根据行数分片

**问题**：对于 GB/TB 级别数据，两次读取效率极低！

### 4.2 新特性：智能行数估算

DataFlow 现在支持多种数据源的行数估算：

```python
from dataflow.utils.storage import (
    FileDataSource,
    S3DataSource,
    HuggingFaceDataSource,
    ModelScopeDataSource,
    FileStorage,
)

# 本地文件 - Parquet 格式（直接读取 metadata，O(1) 复杂度）
source = FileDataSource(
    paths=["data/large_file.parquet"],
    format_type="parquet"
)
total_rows = source.estimate_total_rows()  # 瞬间完成！

# 本地文件 - CSV/JSONL 格式（采样估算）
source = FileDataSource(
    paths=["data/large_file.jsonl"],
    format_type="jsonl"
)
total_rows = source.estimate_total_rows()  # 采样 100 行估算

# S3 文件 - Parquet 格式（只下载 footer，~64KB）
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/large_file.parquet"],
    format_type="parquet"
)
total_rows = source.estimate_total_rows()  # 只下载 64KB！

# HuggingFace 数据集（直接调用 API）
source = HuggingFaceDataSource(
    dataset="openai/gsm8k",
    split="train"
)
total_rows = source.estimate_total_rows()  # 直接返回准确行数
```

### 4.3 分片优化：流式处理

```python
# 旧方法（已废弃）
rows = storage.data_source.read()
total = sum(1 for _ in rows)  # ❌ 需要读取全部数据
storage.split_input(num_partitions=10)

# 新方法（推荐）
storage.split_input(num_partitions=10)  # ✅ 自动估算行数 + 流式分片
# 内部流程：
# 1. estimate_total_rows() → 估算行数
# 2. 流式读取 + 分片 → 一次完成
```

### 4.4 性能对比

| 数据规模 | 旧方法 | 新方法 | 提升 |
|---------|--------|--------|------|
| 1GB JSONL | ~30 秒 | ~5 秒 | 6x |
| 10GB Parquet | ~5 分钟 | ~10 秒 | 30x |
| 100GB S3 Parquet | ~30 分钟 | ~1 分钟 | 30x |

---

## 5. S3 数据源支持

### 5.1 从 S3 读取数据

```python
from dataflow.utils.storage import S3DataSource, S3Storage

# 创建 S3 数据源
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="YOUR_ACCESS_KEY",
    sk="YOUR_SECRET_KEY",
    s3_paths=["s3://your-bucket/input/"],  # 支持目录
    format_type="jsonl"
)

# 创建 Storage
storage = S3Storage(
    data_source=source,
    cache_path="./cache",
    cache_type="jsonl"
)

# 分片并处理
storage.split_input(num_partitions=10)

# 处理步骤 0
storage.batch_step = 0
storage.operator_step = 0
df = storage.read()
print(f"读取了 {len(df)} 行数据")
```

### 5.2 S3 Parquet 优化

对于 S3 上的 Parquet 文件，DataFlow 会智能地只下载 footer（~64KB）获取行数：

```python
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/large.parquet"],
    format_type="parquet"
)

# 估算行数（只下载 64KB，不是整个文件！）
total_rows = source.estimate_total_rows()
print(f"总行数：{total_rows}")
```

---

## 6. 常见问题

### Q1: 如何处理超大文件（>100GB）？

```python
# 使用 S3DataSource + 分片处理
source = S3DataSource(
    endpoint="https://s3.example.com",
    ak="xxx", sk="xxx",
    s3_paths=["s3://bucket/huge_file.jsonl"],
    format_type="jsonl"
)

storage = S3Storage(data_source=source, cache_path="./cache")

# 分成 100 个分片
storage.split_input(num_partitions=100)

# 并行处理
from dataflow.pipeline import PartitionPipelineParallelRun
pipeline = PartitionPipelineParallelRun(storage, partitions=100)
pipeline.compile()
pipeline.forward(max_parallelism=10)  # 10 个并发
```

### Q2: 如何断点续传？

```python
# 第一次运行
pipeline.forward(batch_size=10, resume_from_last=True)

# 中断后继续
pipeline.forward(batch_size=10, resume_from_last=True)  # 从上次中断处继续
```

### Q3: 如何处理多文件格式混合？

```python
from dataflow.utils.storage import FileDataSource

# 混合格式
source = FileDataSource(
    paths=[
        "data/part1.jsonl",
        "data/part2.csv",
        "data/part3.parquet",
    ],
    format_type="jsonl"  # 统一格式（需要数据内容一致）
)
```

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

---

## 🎯 下一步

- [官方文档](https://OpenDCAI.github.io/DataFlow-Doc/)
- [WebUI 教程](#54-webui)
- [示例代码](./test/)
- [Awesome DataFlow](./awesome_dataflow.md)

---

## 📝 更新日志

### v1.1.0 (2026-03-30)
- ✨ 新增 `estimate_total_rows()` 方法，支持智能行数估算
- ⚡ Parquet 格式直接读取 metadata，O(1) 复杂度
- 🚀 S3 Parquet 只下载 footer（~64KB），大幅提升性能
- 🔄 分片优化：流式处理，避免两次读取
- 🐛 修复多个边界情况 Bug

---

<div align="center">

**有问题？查看 [GitHub Issues](https://github.com/OpenDCAI/DataFlow/issues) 或加入 [社区群](#11-community--support)**

</div>
