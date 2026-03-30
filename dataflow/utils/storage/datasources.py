import json
import os
import tempfile

"""
数据源实现模块。

提供 DataSource 的具体实现：
- FileDataSource: 本地文件数据源
- S3DataSource: S3 对象存储数据源
- HuggingFaceDataSource: HuggingFace datasets 数据源
- ModelScopeDataSource: ModelScope 数据源
"""

from pathlib import Path
from typing import Generator, Optional

from tqdm import tqdm

from .iface import DataSource, DataSourceInfo
from .data_parser import get_parser
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
            with open(p, "rb") as f:
                for row in parser.parse_to_dataframe(f, chunk_size=sample_rows):
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
                with open(path, "rb") as f:
                    yield from parser.parse_to_dataframe(f, chunk_size=chunk_size)
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
    ):
        """
        Args:
            endpoint: S3 服务端点
            ak: Access Key ID
            sk: Secret Access Key
            s3_path: S3 路径（可以是文件或目录）
            format_type: 文件格式
        """
        self.endpoint = endpoint
        self.ak = ak
        self.sk = sk
        self.format_type = format_type
        self.s3_paths = self._ensure_files(s3_paths)

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

        # 其他格式：采样解析
        sampled_bytes, sampled_count = 0, 0
        content = read_s3_bytes(client, self.s3_paths[0])

        for row in get_parser(self.format_type).parse_to_dataframe(
            content, chunk_size=100
        ):
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
                content = read_s3_bytes(client, path)
                yield from parser.parse_to_dataframe(content, chunk_size)
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
    **kwargs: dict[str, str],
) -> DataSource:
    """创建数据源实例

    Args:
        path: 数据源路径
        source_type: 数据源类型（自动检测如果为 None）
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
    """
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
