import json
import os
import tempfile
import shutil

from pathlib import Path
from typing import Generator, Optional, Callable

from tqdm import tqdm

from dataflow.core.llm_serving import LLMServingABC
from dataflow.prompts.core_text import FormatStrPrompt

from .iface import DataSource, DataSourceInfo
from .data_parser import get_parser
from .data_cache import LRUCacheManager
from ..s3_plugin import (
    get_s3_client,
    get_s3_object_size,
    list_s3_objects_detailed,
    read_s3_bytes,
)

import pyarrow.parquet as pq


class FileDataSource(DataSource):
    """本地文件数据源"""

    def __init__(self, paths: list[str], format_type: str = "jsonl"):
        """
        Args:
            paths: 文件路径列表
            format_type: 文件格式（jsonl, csv, parquet, json, pickle）
        """
        files: list[str] = []
        for x in paths:
            if x.endswith(f".{format_type}"):
                files.append(x)
                continue
            for y in Path(x).rglob(f"*.{format_type}"):
                files.extend(str(y))
        self.paths = sorted(list(set(files)))
        self.format_type = format_type

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="file", paths=self.paths)

    def estimate_total_rows(self) -> int:
        """通过采样估算总行数

        根据 format_type 采用不同策略：
        - Parquet: 读取 metadata 获取准确行数
        - CSV/JSON/JSONL: 采样解析，计算平均每行字节数
        - Pickle: 无法估算，返回 1
        """
        if not self.paths:
            return 1

        total_size = sum(Path(p).stat().st_size for p in self.paths)
        if total_size == 0:
            return 1

        # Parquet 格式：直接读取 metadata 获取准确行数
        if self.format_type == "parquet":
            total_rows = 0
            for p in self.paths:
                pf = pq.ParquetFile(p)
                total_rows += pf.metadata.num_rows

            return max(1, total_rows)
        # 其他格式：使用 Parser 采样解析
        parser = get_parser(self.format_type)
        sample_rows = 100
        sampled_bytes = 0
        sampled_count = 0

        for p in self.paths:
            for row in parser.parse_to_dataframe(p, chunk_size=sample_rows):
                sampled_bytes += len(
                    (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
                )
                sampled_count += 1
                if sampled_count >= sample_rows:
                    break
            if sampled_count >= sample_rows:
                break

        if sampled_count == 0:
            # 无法采样，用经验值
            return max(1, int(total_size / 200))

        avg_row_bytes = sampled_bytes / sampled_count
        return max(1, int(total_size / avg_row_bytes))

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        parser = get_parser(self.format_type)
        pbar = tqdm(total=len(self.paths), desc="Reading files", unit="file")
        try:
            for path in self.paths:
                pbar.set_postfix(file=Path(path).name[:30])
                # 本地文件，直接解析，无缓存
                yield from parser.parse_to_dataframe(path, chunk_size=chunk_size)
                pbar.update(1)
        finally:
            pbar.close()


class S3DataSource(DataSource):
    """S3 数据源"""

    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
        s3_paths: list[str],
        format_type: str = "jsonl",
        cache_dir: Optional[str] = None,
        cache_max_size_gb: float = 300.0,
    ):
        """
        Args:
            endpoint: S3 服务端点
            ak: Access Key ID
            sk: Secret Access Key
            s3_path: S3 路径（可以是文件或目录）
            format_type: 文件格式
            cache_dir: 缓存目录，None 表示使用默认临时目录
            cache_max_size_gb: 缓存最大大小（GB），默认 300GB
        """
        self.endpoint = endpoint
        self.ak = ak
        self.sk = sk
        self.format_type = format_type
        self.s3_paths = self._ensure_files(s3_paths)

        if cache_dir is None:
            cache_dir = tempfile.gettempdir()

        self.cache_mgr = LRUCacheManager(
            cache_dir=cache_dir,
            max_size_gb=cache_max_size_gb,
            enable_cache=True,
        )

    def _ensure_files(self, s3_paths: list[str]) -> list[str]:
        """懒加载文件列表"""
        client = get_s3_client(self.endpoint, self.ak, self.sk)
        files: list[str] = []
        for x in s3_paths:
            if not x.endswith(f".{self.format_type}"):
                for path, _ in list_s3_objects_detailed(
                    client,
                    x,
                    recursive=True,
                ):
                    if path.endswith(f".{self.format_type}"):
                        files.append(path)
            else:
                files.append(x)
        return files

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="s3", paths=self.s3_paths)

    def estimate_total_rows(self) -> int:
        """通过 S3 对象元数据估算总行数

        根据 format_type 采用不同策略：
        - Parquet: 只下载 footer 获取 metadata（高效）
        - CSV/JSON/JSONL: 采样解析，计算平均每行字节数
        """
        if not self.s3_paths:
            return 1
        client = get_s3_client(self.endpoint, self.ak, self.sk)

        total_size = 0
        for p in self.s3_paths:
            total_size += get_s3_object_size(client, p)
        if total_size == 0:
            return 1

        # Parquet 格式：只下载 footer 获取 metadata（高效，~64KB）
        if self.format_type == "parquet":
            total_rows = 0
            footer_size = 64 * 1024  # Parquet footer 通常在 64KB 内
            for p in self.s3_paths:
                # 获取文件大小
                size = get_s3_object_size(client, p)
                # 只下载尾部 footer
                range_start = max(0, size - footer_size)
                response = read_s3_bytes(
                    client,
                    p,
                    range_start,
                )
                footer_data = response.read()
                # 写入临时文件供 ParquetFile 读取
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".parquet"
                ) as tmp:
                    tmp.write(footer_data)
                    tmp_path = tmp.name
                try:
                    pf = pq.ParquetFile(tmp_path)
                    total_rows += pf.metadata.num_rows
                finally:
                    os.unlink(tmp_path)
            return total_rows

        # 其他格式：采样解析（使用缓存）
        sampled_bytes, sampled_count = 0, 0

        def download_fn(dest):
            content = read_s3_bytes(client, self.s3_paths[0])
            with open(dest, "wb") as f:
                shutil.copyfileobj(content, f)

        parser = get_parser(self.format_type)
        with self.cache_mgr.use(self.s3_paths[0], download_fn) as cache_path:
            for row in parser.parse_to_dataframe(cache_path, chunk_size=100):
                sampled_bytes += len(
                    (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
                )
                sampled_count += 1
                if sampled_count >= 100:
                    break

        avg = sampled_bytes / sampled_count if sampled_count else 200
        return max(1, int(total_size / avg))

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        parser = get_parser(self.format_type)
        client = get_s3_client(self.endpoint, self.ak, self.sk)
        pbar = tqdm(total=len(self.s3_paths), desc="Reading S3", unit="file")
        try:
            for path in self.s3_paths:
                pbar.set_postfix(file=Path(path).name[:30])

                # 缓存逻辑：在 DataSource 层处理
                def download_fn(dest):
                    content = read_s3_bytes(client, path)
                    with open(dest, "wb") as f:
                        shutil.copyfileobj(content, f)

                # 使用缓存管理器
                with self.cache_mgr.use(path, download_fn) as cache_path:
                    yield from parser.parse_to_dataframe(
                        cache_path, chunk_size=chunk_size
                    )

                pbar.update(1)
        finally:
            pbar.close()


class GeneratorDataSource(DataSource):
    """生成器数据源 - 不读取文件，按生成器函数产生数据"""

    def __init__(
        self,
        generator_fn: Callable[[], Generator[dict, None, None]],
        total_rows: int,
        name: str = "generator",
        # LLM 生成相关
        serving: Optional[LLMServingABC] = None,
        prompt_templates: Optional[dict[str, str]] = None,
        fields_from_base: Optional[list[str]] = None,
    ):
        """
        Args:
            generator_fn: 生成器函数，返回基础数据的迭代器（dict）
            total_rows: 总行数（必需，用于进度条和估算）
            name: 数据源名称（用于日志/进度显示）
            serving: LLM Serving 实例（用于生成字段）
            prompt_templates: 字段到 prompt 模板的映射，{字段名: template}
                             template 可以使用 {field_name} 占位符从 base_row 取值
            fields_from_base: 从基础数据中直接复制的字段名列表
        """
        self.generator_fn = generator_fn
        self.total_rows = total_rows
        self.name = name
        self.serving = serving
        self.prompt_templates = prompt_templates or {}
        self.fields_from_base = fields_from_base or []
        self._info: Optional[DataSourceInfo] = None

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="generator", paths=[self.name])

    def estimate_total_rows(self) -> int:
        """返回总行数"""
        return self.total_rows

    def _generate_fields_with_llm(self, base_rows: list[dict]) -> list[dict]:
        """使用 LLM 批量生成指定字段"""
        if not self.serving or not self.prompt_templates:
            # 没有 serving 或 prompt_templates，直接返回基础数据
            return [{k: row.get(k) for k in self.fields_from_base} for row in base_rows]

        import json

        results = []

        # 为每个字段批量调用 LLM
        for field_name, prompt_template in self.prompt_templates.items():
            # 构建批量输入：每个 base_row 格式化一次 prompt
            inputs = []
            for row in base_rows:
                try:
                    inputs.append(prompt_template.format(**row))
                except KeyError:
                    # 如果 base_row 中没有对应的 key，使用原始 template
                    inputs.append(prompt_template)

            # 批量调用 LLM
            responses = self.serving.generate_from_input(inputs, system_prompt="")

            # 解析响应
            for i, response in enumerate(responses):
                if i >= len(results):
                    # 复制基础字段
                    results.append(
                        {k: base_rows[i].get(k) for k in self.fields_from_base}
                    )

                try:
                    parsed = json.loads(response)
                    if isinstance(parsed, dict) and field_name in parsed:
                        results[i][field_name] = parsed[field_name]
                    else:
                        results[i][field_name] = parsed
                except json.JSONDecodeError:
                    results[i][field_name] = response

        return results

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        """批量生成数据"""
        pbar = tqdm(
            total=self.total_rows,
            desc=f"Generating {self.name}",
            unit="row",
        )
        try:
            batch = []
            count = 0
            for base_row in self.generator_fn():
                if count >= self.total_rows:
                    break
                batch.append(base_row)
                count += 1
                if len(batch) >= chunk_size:
                    # 批量生成
                    results = self._generate_fields_with_llm(batch)
                    for row in results:
                        yield row
                        pbar.update(1)
                    batch = []

            # 处理剩余的 batch
            if batch:
                results = self._generate_fields_with_llm(batch)
                for row in results:
                    yield row
                    pbar.update(1)
        finally:
            pbar.close()


class LLMGeneratorDataSource(DataSource):
    """LLM 生成器数据源 - 完全由 LLM 生成数据"""

    def __init__(
        self,
        serving: LLMServingABC,
        prompts: dict[str, str],
        num_rows: int,
        name: str = "llm_generator",
        batch_size: int = 32,
    ):
        """
        Args:
            serving: LLM Serving 实例
            prompts: 字段到 prompt 的映射，{字段名: prompt}
            num_rows: 要生成的行数
            name: 数据源名称
            batch_size: 批处理大小
        """
        self.serving = serving
        self.prompts = prompts
        self.num_rows = num_rows
        self.name = name
        self.batch_size = batch_size
        self._info: Optional[DataSourceInfo] = None

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="llm_generator", paths=[self.name])

    def estimate_total_rows(self) -> int:
        return self.num_rows

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        """使用 LLM 批量生成数据"""
        import json

        pbar = tqdm(
            total=self.num_rows,
            desc=f"LLM Generating {self.name}",
            unit="row",
        )
        try:
            # 生成所有输入（每个字段一个 batch）
            results = [{} for _ in range(self.num_rows)]

            for field_name, prompt in self.prompts.items():
                # 批量输入：每个 row 都使用相同的 prompt
                inputs = [prompt for _ in range(self.num_rows)]

                # 批量调用 LLM
                responses = self.serving.generate_from_input(inputs, system_prompt="")

                # 解析响应
                for i, response in enumerate(responses):
                    try:
                        parsed = json.loads(response)
                        if isinstance(parsed, dict) and field_name in parsed:
                            results[i][field_name] = parsed[field_name]
                        else:
                            results[i][field_name] = parsed
                    except json.JSONDecodeError:
                        results[i][field_name] = response

            # 逐行 yield
            for row in results:
                yield row
                pbar.update(1)
        finally:
            pbar.close()


class HuggingFaceDataSource(DataSource):
    """HuggingFace datasets 数据源"""

    def __init__(
        self,
        dataset: str,
        split: str = "train",
        streaming: bool = True,
        **kwargs,
    ):
        """
        Args:
            dataset: HuggingFace 数据集名称（"username/dataset_name"）
            split: 数据集划分（train/test/validation）
            streaming: 是否使用流式读取
            **kwargs: 传递给 load_dataset 的其他参数
        """
        self.dataset = dataset
        self.split = split
        self.streaming = streaming
        self.kwargs = kwargs
        self._dataset = None

    def _ensure_dataset(self):
        """懒加载数据集"""
        if self._dataset is None:
            from datasets import load_dataset

            self._dataset = load_dataset(
                self.dataset,
                split=self.split,
                streaming=self.streaming,
                **self.kwargs,
            )

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="huggingface", paths=[self.dataset])

    def estimate_total_rows(self) -> int:
        self._ensure_dataset()
        return len(self.dataset)

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        self._ensure_dataset()
        # 对于流式数据集，无法预知总数，使用动态进度条
        pbar = tqdm(desc=f"Loading {self.dataset}", unit="row")
        try:
            for row in self._dataset:
                yield dict(row)
                pbar.update(1)
        finally:
            pbar.close()


class ModelScopeDataSource(DataSource):
    """ModelScope 数据源"""

    def __init__(self, dataset: str, split: str = "train", **kwargs):
        """
        Args:
            dataset: ModelScope 数据集名称
            split: 数据集划分
            **kwargs: 传递给 load_dataset 的其他参数
        """
        self.dataset = dataset
        self.split = split
        self.kwargs = kwargs
        self._dataset = None

    def _ensure_dataset(self):
        """懒加载数据集"""
        if self._dataset is None:
            from modelscope.msdatasets import load_dataset

            self._dataset = load_dataset(self.dataset, split=self.split, **self.kwargs)

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="modelscope", paths=[self.dataset])

    def estimate_total_rows(self) -> int:
        self._ensure_dataset()
        return len(self.dataset)

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        self._ensure_dataset()
        # 对于流式数据集，无法预知总数，使用动态进度条
        pbar = tqdm(desc=f"Loading {self.dataset}", unit="row")
        try:
            for row in self._dataset:
                yield dict(row)
                pbar.update(1)
        finally:
            pbar.close()


# ==================== 工厂函数 ====================


def create_data_source(
    paths: list[str],
    source_type: Optional[str] = None,
    generator_fn: Optional[Callable[[], Generator[dict, None, None]]] = None,
    total_rows: Optional[int] = None,
    serving: Optional[object] = None,
    prompt_templates: Optional[dict[str, str]] = None,
    fields_from_base: Optional[list[str]] = None,
    num_rows: Optional[int] = None,
    **kwargs: dict[str, str],
) -> DataSource:
    """创建数据源实例

    Args:
        paths: 数据源路径列表
        source_type: 数据源类型（自动检测如果为 None）
        generator_fn: 生成器函数（仅用于 generator 类型）
        total_rows: 总行数（仅用于 generator 类型）
        serving: LLM Serving 实例（用于 generator/llm_generator 类型）
        prompt_templates: 字段到 prompt 模板的映射 {字段名: template}（用于 generator 类型）
                         template 可以使用 {field_name} 占位符从 base_row 取值
        fields_from_base: 从基础数据中直接复制的字段名列表（仅用于 generator 类型）
        num_rows: 要生成的行数（仅用于 llm_generator 类型）
        **kwargs: 数据源特定参数

    Returns:
        DataSource 实例

    Example:
        # 自动检测
        source = create_data_source("s3://bucket/data/")
        source = create_data_source("/local/path/data.jsonl")

        # 显式指定
        source = create_data_source(
            "username/dataset",
            source_type="huggingface",
            split="train"
        )

        # 生成器数据源（基础数据 + LLM 增强）
        def my_generator():
            for i in range(1000):
                yield {"index": i, "scene": "search", "keywords": "特斯拉"}

        source = create_data_source(
            ["enhanced_tasks"],
            source_type="generator",
            generator_fn=my_generator,
            total_rows=1000,
            serving=llm_serving,
            prompt_templates={
                "question": "基于场景 {scene} 和关键词 {keywords}，生成一个真实的搜索问题。返回 JSON: {{\"question\": \"...\"}}",
                "target_skills": "基于场景 {scene}，选择适合的技能。返回 JSON: {{\"target_skills\": [...]}}",
            },
            fields_from_base=["index", "scene", "keywords"],
        )

        # 纯 LLM 生成数据源
        source = create_data_source(
            ["llm_tasks"],
            source_type="llm_generator",
            serving=llm_serving,
            prompts={
                "question": "生成一个真实的技能使用问题。返回 JSON: {{\"question\": \"...\"}}",
                "target_skills": "选择适合的技能。返回 JSON: {{\"target_skills\": [...]}}",
            },
            num_rows=10000,
        )
    """
    if source_type == "generator":
        if generator_fn is None:
            raise ValueError("generator_fn is required for generator data source")
        return GeneratorDataSource(
            generator_fn=generator_fn,
            total_rows=total_rows,
            name=paths[0] if paths else "generator",
            serving=serving,
            prompt_templates=prompt_templates,
            fields_from_base=fields_from_base,
        )
    elif source_type == "llm_generator":
        if serving is None:
            raise ValueError("serving is required for llm_generator data source")
        if prompts is None:
            raise ValueError("prompts is required for llm_generator data source")
        if num_rows is None:
            raise ValueError("num_rows is required for llm_generator data source")
        return LLMGeneratorDataSource(
            serving=serving,
            prompts=prompts,
            num_rows=num_rows,
            name=paths[0] if paths else "llm_generator",
        )

    path = paths[0]
    if source_type is None:
        # 自动检测
        if path.startswith("s3://") or path.startswith("s3a://"):
            source_type = "s3"
        elif path.startswith("hf://") or "/" in path and "." not in path.split("/")[-1]:
            source_type = "huggingface"
        elif path.startswith("ms://"):
            source_type = "modelscope"
        else:
            source_type = "file"

    if source_type == "s3":
        return S3DataSource(
            endpoint=kwargs.get("endpoint"),
            ak=kwargs.get("ak"),
            sk=kwargs.get("sk"),
            s3_paths=paths,
            format_type=kwargs.get("format_type", "jsonl"),
        )
    elif source_type == "file":
        return FileDataSource(
            paths=paths,
            format_type=kwargs.get("format_type", "jsonl"),
        )
    elif source_type == "huggingface":
        return HuggingFaceDataSource(
            dataset=path,
            split=kwargs.get("split", "train"),
            streaming=kwargs.get("streaming", True),
            **{k: v for k, v in kwargs.items() if k not in ["split", "streaming"]},
        )
    elif source_type == "modelscope":
        return ModelScopeDataSource(
            dataset=path,
            split=kwargs.get("split", "train"),
            **{k: v for k, v in kwargs.items() if k != "split"},
        )
    else:
        raise ValueError(f"Unsupported source type: {source_type}")
