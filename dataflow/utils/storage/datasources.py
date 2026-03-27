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
from ..s3_plugin import get_s3_client, list_s3_objects_detailed, read_s3_bytes


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
            for y in Path(x).rglob(f"*.{format_type}"):
                files.extend(str(y))
        self.paths = sorted(list(set(files)))
        self.format_type = format_type

    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="file", paths=self.paths)

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
