"""
数据解析器模块 - 负责文件格式与 DataFrame 之间的转换。

职责：file-like <-> DataFrame 的序列化/反序列化
Storage 负责 bytes 的读写，文件格式解析由 Parser 负责。
"""

from abc import ABC, abstractmethod
from typing import Any, Union

import io
import shutil
import tempfile

import pandas as pd

from dataflow.logger import get_logger

logger = get_logger()


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

    @abstractmethod
    def parse_to_dataframe(self, data: Any) -> pd.DataFrame:
        """将 file-like 对象（BytesIO、StreamingBody）解析为 DataFrame。

        Args:
            data: file-like object with read() method

        Returns:
            解析后的 DataFrame

        Raises:
            ValueError: 解析失败时抛出
        """
        pass

    @abstractmethod
    def serialize_to_file(self, df: pd.DataFrame, dst: Union[str, io.IOBase]) -> None:
        """将 DataFrame 序列化到文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径或文件对象

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

    def parse_to_dataframe(self, data: Any) -> pd.DataFrame:
        """将 JSON file-like 对象解析为 DataFrame。

        Args:
            data: JSON file-like object

        Returns:
            解析后的 DataFrame

        Raises:
            ValueError: JSON 解析失败时抛出
        """
        try:
            # 统一使用临时文件 + 流式拷贝，避免内存翻倍，代码逻辑一致
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                shutil.copyfileobj(data, tmp)
                tmp_path = tmp.name
            try:
                return pd.read_json(tmp_path)
            finally:
                import os

                os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"❌ JSON 解析失败：{e}")
            raise ValueError(f"JSON 解析失败：{e}")

    def serialize_to_file(self, df: pd.DataFrame, dst: Union[str, io.IOBase]) -> None:
        """将 DataFrame 序列化为 JSON 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径或文件对象

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
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

    def parse_to_dataframe(self, data: Any) -> pd.DataFrame:
        """将 JSONL file-like 对象解析为 DataFrame。

        Args:
            data: JSONL file-like object

        Returns:
            解析后的 DataFrame

        Raises:
            ValueError: JSONL 解析失败时抛出
        """
        try:
            # 统一使用临时文件 + 流式拷贝，避免内存翻倍
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                shutil.copyfileobj(data, tmp)
                tmp_path = tmp.name
            try:
                return pd.read_json(tmp_path, lines=True)
            finally:
                import os

                os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"❌ JSONL 解析失败：{e}")
            raise ValueError(f"JSONL 解析失败：{e}")

    def serialize_to_file(self, df: pd.DataFrame, dst: Union[str, io.IOBase]) -> None:
        """将 DataFrame 序列化为 JSONL 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径或文件对象

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
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

    def parse_to_dataframe(self, data: Any) -> pd.DataFrame:
        """将 CSV file-like 对象解析为 DataFrame。

        Args:
            data: CSV file-like object

        Returns:
            解析后的 DataFrame

        Raises:
            ValueError: CSV 解析失败时抛出
        """
        try:
            # 统一使用临时文件 + 流式拷贝，避免内存翻倍
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                shutil.copyfileobj(data, tmp)
                tmp_path = tmp.name
            try:
                return pd.read_csv(tmp_path)
            finally:
                import os

                os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"❌ CSV 解析失败：{e}")
            raise ValueError(f"CSV parsing failed: {e}")

    def serialize_to_file(self, df: pd.DataFrame, dst: Union[str, io.IOBase]) -> None:
        """将 DataFrame 序列化为 CSV 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径或文件对象

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
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

    def parse_to_dataframe(self, data: Any) -> pd.DataFrame:
        """将 Parquet file-like 对象解析为 DataFrame。

        Args:
            data: Parquet file-like object

        Returns:
            解析后的 DataFrame

        Raises:
            ValueError: Parquet 解析失败时抛出
        """
        try:
            # 统一使用临时文件 + 流式拷贝，避免内存翻倍
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                shutil.copyfileobj(data, tmp)
                tmp_path = tmp.name
            try:
                return pd.read_parquet(tmp_path)
            finally:
                import os

                os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"❌ Parquet 解析失败：{e}")
            raise ValueError(f"Parquet parsing failed: {e}")

    def serialize_to_file(self, df: pd.DataFrame, dst: Union[str, io.IOBase]) -> None:
        """将 DataFrame 序列化为 Parquet 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径或文件对象

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
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

    def parse_to_dataframe(self, data: Any) -> pd.DataFrame:
        """将 Pickle file-like 对象解析为 DataFrame。

        Args:
            data: Pickle file-like object

        Returns:
            解析后的 DataFrame

        Raises:
            ValueError: Pickle 解析失败时抛出

        Warning:
            Pickle 格式存在安全风险，仅用于受信任的数据源。
        """
        try:
            # 统一使用临时文件 + 流式拷贝，避免内存翻倍
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                shutil.copyfileobj(data, tmp)
                tmp_path = tmp.name
            try:
                return pd.read_pickle(tmp_path)
            finally:
                import os

                os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"❌ Pickle 解析失败：{e}")
            raise ValueError(f"Pickle parsing failed: {e}")

    def serialize_to_file(self, df: pd.DataFrame, dst: Union[str, io.IOBase]) -> None:
        """将 DataFrame 序列化为 Pickle 文件。

        Args:
            df: 要序列化的 DataFrame
            dst: 目标文件路径或文件对象

        Raises:
            ValueError: 序列化失败时抛出
        """
        try:
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


def parse_bytes_to_dataframe(data: Any, format_type: str) -> pd.DataFrame:
    """便捷函数：将 file-like 对象（BytesIO、StreamingBody）解析为 DataFrame。

    Args:
        data: file-like object with read() method
        format_type: 格式类型

    Returns:
        解析后的 DataFrame
    """
    return get_parser(format_type).parse_to_dataframe(data)


def serialize_dataframe_to_bytes(df: pd.DataFrame, format_type: str) -> bytes:
    """便捷函数：将 DataFrame 序列化为 bytes。

    Args:
        df: 要序列化的 DataFrame
        format_type: 格式类型

    Returns:
        序列化后的 bytes
    """
    buffer = io.BytesIO()
    get_parser(format_type).serialize_to_file(df, buffer)
    buffer.seek(0)
    return buffer.getvalue()
