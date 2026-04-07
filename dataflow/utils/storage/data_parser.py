"""
数据解析器模块 - 负责文件格式与 DataFrame 之间的转换。

职责：file-like (BytesIO/StreamingBody) <-> DataFrame 的序列化/反序列化
Storage 负责 bytes 的读写，文件格式解析由 Parser 负责。
"""

from abc import ABC, abstractmethod
from typing import Any, Union, Generator

import io
import json
import os
import shutil
import tempfile

import pandas as pd

from botocore.response import StreamingBody
from dataflow.logger import get_logger

logger = get_logger()


def clean_surrogates(obj: Any) -> Any:
    """递归清理字符串中的 Unicode surrogate 字符。

    Unicode surrogate 字符（U+D800-U+DFFF）在 UTF-8 中是无效的，
    会导致编码错误。此函数遍历对象中的所有字符串，
    移除或替换这些无效字符。

    Args:
        obj: 任意对象（str, dict, list, 或其他类型）

    Returns:
        清理后的对象，保持原类型

    Example:
        # 清理单个字符串
        cleaned = clean_surrogates("hello\\udfe8 world")

        # 清理嵌套数据结构
        data = {"text": "hello\\udfe8", "list": ["item\\udfe8"]}
        cleaned = clean_surrogates(data)
    """
    if isinstance(obj, str):
        # 移除所有 surrogate 字符（U+D800 到 U+DFFF）
        return "".join(c for c in obj if not ("\ud800" <= c <= "\udfff"))
    elif isinstance(obj, dict):
        return {k: clean_surrogates(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return type(obj)(clean_surrogates(item) for item in obj)
    else:
        return obj


class DataParser(ABC):
    """数据解析器抽象基类。

    负责将 DataFrame 序列化为 bytes，或将 bytes 反序列化为 DataFrame。
    Storage 实现通过此接口处理不同文件格式，无需关心具体格式细节。

    支持的格式：
    - JSON: JsonParser
    - JSONL: JsonlParser
    - CSV: CsvParser
    - Parquet: ParquetParser
    - Pickle: PickleParser
    """

    @staticmethod
    def _clean_data_for_serialization(data: Any) -> Any:
        """清理数据中的 Unicode surrogate 字符，准备序列化。

        在序列化前调用此方法清理 DataFrame 或 dict 中的无效字符。

        Args:
            data: 要清理的数据（DataFrame, dict, list 等）

        Returns:
            清理后的数据
        """
        if isinstance(data, pd.DataFrame):
            # 对 DataFrame 的每一列进行处理
            cleaned_data = data.copy()
            for col in cleaned_data.columns:
                if cleaned_data[col].dtype == "object":
                    cleaned_data[col] = cleaned_data[col].apply(clean_surrogates)
            return cleaned_data
        else:
            return clean_surrogates(data)

    @abstractmethod
    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """将 file-like 对象或文件路径解析为逐行记录。

        Args:
            data: file-like object 或文件路径 (str)
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录

        Raises:
            ValueError: 解析失败时抛出
        """
        pass

    @abstractmethod
    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """将 DataFrame 序列化到文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径

        Raises:
            ValueError: 序列化失败时抛出
        """
        pass


class JsonParser(DataParser):
    """JSON 格式解析器。

    特性：
    - 解析：pd.read_json() 默认 orient="auto"
    - 序列化：orient="records", force_ascii=False, indent=2
    """

    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """将 JSON file-like 对象解析为逐行记录。

        Args:
            data: BytesIO 或 StreamingBody 对象
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录

        Raises:
            ValueError: JSON 解析失败时抛出
        """
        # file-like 对象：先拷贝到临时文件
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            shutil.copyfileobj(data, tmp)
            file_path = tmp.name

        try:
            for row in pd.read_json(file_path).to_dict("records"):
                yield row
        except Exception as e:
            logger.error(f"❌ JSON 解析失败：{e}")
            raise ValueError(f"JSON 解析失败：{e}")
        finally:
            os.unlink(file_path)

    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """将 DataFrame 序列化为 JSON 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
            # 清理 Unicode surrogate 字符
            df = self._clean_data_for_serialization(df)
            df.to_json(dst, orient="records", force_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ JSON 序列化失败：{e}")
            raise ValueError(f"JSON 序列化失败：{e}")


class JsonlParser(DataParser):
    """JSONL 格式解析器（每行一个 JSON 对象）。

    特性：
    - 适用于流式处理和大文件
    - 每行一个独立的 JSON 对象
    - 解析：lines=True
    - 序列化：orient="records", lines=True
    """

    total_read_file = 0

    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """将 JSONL file-like 对象解析为逐行记录。

        Args:
            data: BytesIO 或 StreamingBody 对象
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录

        Raises:
            ValueError: JSONL 解析失败时抛出
        """
        self.total_read_file += 1
        # file-like 对象：先拷贝到临时文件
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=f".{self.total_read_file}"
        ) as tmp:
            shutil.copyfileobj(data, tmp)
            file_path = tmp.name

        try:
            with open(file_path) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        yield d
                    except Exception as e:
                        logger.warning(f"skip json line: {line[:100]}")
            # for chunk in pd.read_json(file_path, lines=True, chunksize=chunk_size, engine="pyarrow"):
            #     chunk: pd.DataFrame
            #     for _, row in chunk.iterrows():
            #         yield row.to_dict()
        except Exception as e:
            logger.error(f"❌ JSONL 解析失败：{e}")
            raise ValueError(f"JSONL 解析失败：{e}")
        finally:
            os.unlink(file_path)

    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """将 DataFrame 序列化为 JSONL 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
            # 清理 Unicode surrogate 字符
            df = self._clean_data_for_serialization(df)
            df.to_json(dst, orient="records", lines=True, force_ascii=False)
        except Exception as e:
            logger.error(f"❌ JSONL 序列化失败：{e}")
            raise ValueError(f"JSONL 序列化失败：{e}")


class CsvParser(DataParser):
    """CSV 格式解析器。

    特性：
    - 解析：pd.read_csv() 默认逗号分隔
    - 序列化：index=False（不写入行索引）
    """

    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """将 CSV file-like 对象解析为逐行记录。

        Args:
            data: BytesIO 或 StreamingBody 对象
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录

        Raises:
            ValueError: CSV 解析失败时抛出
        """
        # file-like 对象：先拷贝到临时文件
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            shutil.copyfileobj(data, tmp)
            file_path = tmp.name

        try:
            for chunk in pd.read_csv(file_path, chunksize=chunk_size):
                chunk: pd.DataFrame
                for _, row in chunk.iterrows():
                    yield row.to_dict()
        except Exception as e:
            logger.error(f"❌ CSV 解析失败：{e}")
            raise ValueError(f"CSV 解析失败：{e}")
        finally:
            os.unlink(file_path)

    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """将 DataFrame 序列化为 CSV 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
            # 清理 Unicode surrogate 字符
            df = self._clean_data_for_serialization(df)
            df.to_csv(dst, index=False)
        except Exception as e:
            logger.error(f"❌ CSV 序列化失败：{e}")
            raise ValueError(f"CSV serialization failed: {e}")


class ParquetParser(DataParser):
    """Parquet 格式解析器。

    特性：
    - 列式存储，适合大规模数据分析
    - 支持数据类型推断和压缩
    - 解析：pd.read_parquet()
    - 序列化：df.to_parquet()
    """

    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """将 Parquet file-like 对象解析为逐行记录。

        Args:
            data: BytesIO 或 StreamingBody 对象
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录

        Raises:
            ValueError: Parquet 解析失败时抛出
        """
        # file-like 对象：先拷贝到临时文件
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            shutil.copyfileobj(data, tmp)
            file_path = tmp.name

        try:
            for row in pd.read_parquet(file_path).to_dict("records"):
                yield row
        except Exception as e:
            logger.error(f"❌ Parquet 解析失败：{e}")
            raise ValueError(f"Parquet 解析失败：{e}")
        finally:
            os.unlink(file_path)

    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """将 DataFrame 序列化为 Parquet 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
            # 清理 Unicode surrogate 字符
            df = self._clean_data_for_serialization(df)
            df.to_parquet(dst, index=False)
        except Exception as e:
            logger.error(f"❌ Parquet 序列化失败：{e}")
            raise ValueError(f"Parquet 序列化失败：{e}")


class PickleParser(DataParser):
    """Pickle 格式解析器。

    特性：
    - Python 原生序列化格式
    - 保留完整数据类型信息
    - 注意：仅用于受信任的数据源（安全风险）
    """

    def parse_to_dataframe(
        self,
        data: Union[io.BytesIO, StreamingBody],
        chunk_size: int = 1000,
    ) -> Generator[dict, None, None]:
        """将 Pickle file-like 对象解析为逐行记录。

        Args:
            data: BytesIO 或 StreamingBody 对象
            chunk_size: 每批读取的行数

        Yields:
            逐行返回 dict 记录

        Raises:
            ValueError: Pickle 解析失败时抛出

        Warning:
            Pickle 格式存在安全风险，仅用于受信任的数据源。
        """
        # file-like 对象：先拷贝到临时文件
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            shutil.copyfileobj(data, tmp)
            file_path = tmp.name

        try:
            # Pickle 不支持 chunksize，需要全部加载
            df = pd.read_pickle(file_path)
            for _, row in df.iterrows():
                yield row.to_dict()
        except Exception as e:
            logger.error(f"❌ Pickle 解析失败：{e}")
            raise ValueError(f"Pickle 解析失败：{e}")
        finally:
            os.unlink(file_path)

    def serialize_to_file(self, df: pd.DataFrame, dst: str) -> None:
        """将 DataFrame 序列化为 Pickle 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
            # 清理 Unicode surrogate 字符
            df = self._clean_data_for_serialization(df)
            df.to_pickle(dst)
        except Exception as e:
            logger.error(f"❌ Pickle 序列化失败：{e}")
            raise ValueError(f"Pickle serialization failed: {e}")


# 格式到解析器的映射
PARSER_REGISTRY: dict[str, type[DataParser]] = {
    "json": JsonParser,
    "jsonl": JsonlParser,
    "csv": CsvParser,
    "parquet": ParquetParser,
    "pickle": PickleParser,
}


def get_parser(format_type: str) -> DataParser:
    """获取指定格式的解析器。

    Args:
        format_type: 格式类型（"json", "jsonl", "csv", "parquet", "pickle"）

    Returns:
        对应的解析器实例

    Raises:
        ValueError: 不支持的格式类型
    """
    format_lower = format_type.lower()
    parser_class = PARSER_REGISTRY.get(format_lower)
    if parser_class is None:
        logger.error(
            f"Unsupported format: {format_type}. Supported: {list(PARSER_REGISTRY.keys())}"
        )
        raise ValueError(
            f"Unsupported format: {format_type}. "
            f"Supported formats: {list(PARSER_REGISTRY.keys())}"
        )
    logger.debug(f"🔧 使用解析器：{format_lower}")
    return parser_class()
