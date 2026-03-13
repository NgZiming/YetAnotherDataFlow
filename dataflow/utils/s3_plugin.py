"""
S3 存储插件模块。

提供 S3 对象存储的通用工具函数和存储类，支持：
- S3 路径解析和对象列表
- 媒体文件读取（S3/本地）
- JSONL 格式的分布式存储（支持断点续传、分批处理）

主要类：
    - MediaStorage: 媒体存储抽象基类
    - S3MediaStorage: S3 媒体存储实现
    - FileMediaStorage: 本地文件媒体存储实现
    - S3JsonlStorage: S3 JSONL 存储实现（继承自 DataFlowStorage）
"""

import copy
import json
import re

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator, Optional, Literal

import boto3
import pandas as pd

from botocore.client import Config
from botocore.exceptions import ClientError, ResponseStreamingError

from dataflow import get_logger
from dataflow.utils.storage import DataFlowStorage

# S3 路径匹配正则表达式，支持 s3:// 和 s3a:// 协议
__re_s3_path = re.compile("^s3a?://([^/]+)(?:/(.*))?$")


def split_s3_path(path: str):
    """解析 S3 路径，提取 bucket 和 key。

    Args:
        path: S3 路径，格式为 s3://bucket/key 或 s3a://bucket/key

    Returns:
        tuple: (bucket_name, object_key) 元组，如果解析失败则返回 ("", "")

    Example:
        >>> split_s3_path("s3://my-bucket/path/to/file.jsonl")
        ('my-bucket', 'path/to/file.jsonl')
    """
    m = __re_s3_path.match(path)
    if m is None:
        return "", ""
    return m.group(1), (m.group(2) or "")


def list_s3_objects_detailed(
    client, path: str, recursive=False, is_prefix=False, limit=0
):
    """列出 S3 目录下的对象，支持分页和递归。

    Args:
        client: boto3 S3 client 对象
        path: S3 路径（s3://bucket/prefix）
        recursive: 是否递归列出子目录，False 时只列出当前层
        is_prefix: 是否作为前缀匹配，False 时会在路径末尾添加 /
        limit: 最大返回数量，0 表示无限制

    Yields:
        tuple: (s3_path, metadata) 元组
            - 如果是目录（recursive=False），返回 (s3://bucket/prefix/, common_prefix_info)
            - 如果是文件，返回 (s3://bucket/key, content_info)

    Note:
        - 支持分页处理大量对象
        - recursive=False 时会通过 Delimiter 参数区分文件和目录
        - 使用 Marker 实现分页遍历
    """
    if limit > 1000:
        raise Exception("limit greater than 1000 is not supported.")
    if not path.endswith("/") and not is_prefix:
        path += "/"
    bucket, prefix = split_s3_path(path)
    marker = None
    while True:
        list_kwargs = dict(MaxKeys=1000, Bucket=bucket, Prefix=prefix)
        if limit > 0:
            list_kwargs["MaxKeys"] = limit
        if not recursive:
            list_kwargs["Delimiter"] = "/"
        if marker:
            list_kwargs["Marker"] = marker
        response = client.list_objects(**list_kwargs)
        marker = None
        if not recursive:
            common_prefixes = response.get("CommonPrefixes", [])
            for cp in common_prefixes:
                yield (f"s3://{bucket}/{cp['Prefix']}", cp)
            if common_prefixes:
                marker = common_prefixes[-1]["Prefix"]
        contents = response.get("Contents", [])
        for content in contents:
            if not content["Key"].endswith("/"):
                yield (f"s3://{bucket}/{content['Key']}", content)
        if contents:
            last_key = contents[-1]["Key"]
            if not marker or last_key > marker:
                marker = last_key
        if limit or not response.get("IsTruncated") or not marker:
            break


def get_s3_client(endpoint: str, ak: str, sk: str):
    """创建 S3 客户端。

    配置了长超时和重试策略，适用于大规模数据传输场景。

    Args:
        endpoint: S3 服务端点 URL
        ak: 访问密钥 ID (Access Key ID)
        sk: 秘密访问密钥 (Secret Access Key)

    Returns:
        boto3.client: 配置好的 S3 客户端

    Note:
        - addressing_style: path - 使用路径风格访问（兼容各种 S3 兼容存储）
        - max_attempts: 8 - 最多重试 8 次
        - connect_timeout/read_timeout: 600s - 适合大文件传输
    """
    return boto3.client(
        "s3",
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        endpoint_url=endpoint,
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 8, "mode": "standard"},
            connect_timeout=600,
            read_timeout=600,
        ),
    )


def exists_s3_object(client, s3_path: str) -> bool:
    """检查 S3 对象是否存在。

    Args:
        client: boto3 S3 client 对象
        s3_path: S3 路径

    Returns:
        bool: 对象存在返回 True，不存在返回 False

    Raises:
        ClientError: 除 404 以外的其他错误会抛出异常
    """
    try:
        bucket, key = split_s3_path(s3_path)
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise e


def read_s3_bytes(client, s3_path: str) -> bytes:
    """读取 S3 对象的完整内容。

    Args:
        client: boto3 S3 client 对象
        s3_path: S3 路径

    Returns:
        bytes: 对象的全部内容

    Note:
        适用于读取中小型文件，大文件建议使用流式读取。
    """
    bucket_name, object_key = split_s3_path(s3_path)
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    streaming_body = response["Body"]
    return streaming_body.read()


class MediaStorage(ABC):
    """媒体存储抽象基类。

    定义读取媒体文件字节数据的接口，支持不同存储后端。
    """

    @abstractmethod
    def read_media_bytes(self, media_path: str) -> bytes:
        """读取媒体文件的字节数据。

        Args:
            media_path: 媒体文件路径（具体格式取决于实现类）

        Returns:
            bytes: 媒体文件的字节内容

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError


class S3MediaStorage(MediaStorage):
    """S3 媒体存储实现。

    从 S3 读取媒体文件（图片、视频等），适用于多模态数据处理场景。

    Args:
        endpoint: S3 服务端点 URL
        ak: 访问密钥 ID
        sk: 秘密访问密钥

    Example:
        >>> storage = S3MediaStorage("https://s3.example.com", "ak", "sk")
        >>> img_bytes = storage.read_media_bytes("s3://bucket/images/photo.jpg")
    """

    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
    ) -> None:
        self.endpoint = endpoint
        self.ak = ak
        self.sk = sk

    def read_media_bytes(self, media_path: str) -> bytes:
        """从 S3 读取媒体文件。

        Args:
            media_path: S3 路径，格式为 s3://bucket/key

        Returns:
            bytes: 媒体文件的字节内容
        """
        return read_s3_bytes(get_s3_client(self.endpoint, self.ak, self.sk), media_path)


class FileMediaStorage(MediaStorage):
    """本地文件媒体存储实现。

    从本地文件系统读取媒体文件，适用于本地开发和测试。
    """

    def __init__(self) -> None:
        pass

    def read_media_bytes(self, media_path: str) -> bytes:
        """从本地文件读取媒体数据。

        Args:
            media_path: 本地文件路径

        Returns:
            bytes: 文件的字节内容
        """
        return open(media_path, "rb").read()


class S3JsonlStorage(DataFlowStorage):
    """S3 JSONL 格式存储实现。

    支持将数据以 JSONL（每行一个 JSON 对象）格式存储在 S3，
    适用于大规模数据集的分布式处理场景。

    核心特性：
        - **分批处理**：支持按 batch_size 分批读取和写入
        - **断点续传**：通过 batch_step 记录处理进度
        - **分步处理**：通过 operator_step 支持多 operator 流水线
        - **流式读取**：支持增量读取，避免一次性加载大量数据

    Args:
        endpoint: S3 服务端点 URL
        ak: 访问密钥 ID
        sk: 秘密访问密钥
        s3_paths: 输入数据路径列表（step=0 时使用）
        output_s3_path: 输出数据的基础路径

    Example:
        >>> storage = S3JsonlStorage(
        ...     endpoint="https://s3.example.com",
        ...     ak="xxx", sk="xxx",
        ...     s3_paths=["s3://bucket/input/data.jsonl"],
        ...     output_s3_path="s3://bucket/output"
        ... )
        >>> df = storage.read()
        >>> storage.write(df)
    """

    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
        s3_paths: list[str],
        output_s3_path: str,
    ) -> None:
        self.client = get_s3_client(endpoint, ak, sk)
        self.s3_paths = s3_paths
        self.output_s3_path = output_s3_path

        self._batch_step = 0
        self._batch_size = None
        self.operator_step = -1
        self.logger = get_logger()

        self._current_streaming_chunk: Optional[pd.DataFrame] = None

    @property
    def batch_step(self) -> int:
        """当前批次步骤索引（从 0 开始）"""
        return self._batch_step

    @batch_step.setter
    def batch_step(self, new_value: int) -> int:
        self._batch_step = new_value
        return self._batch_step

    @property
    def batch_size(self) -> Optional[int]:
        """批次大小，None 表示不分批"""
        return self._batch_size

    @batch_size.setter
    def batch_size(self, new_value: Optional[int]) -> Optional[int]:
        self._batch_size = new_value
        return self._batch_size

    @property
    def current_streaming_chunk(self) -> Optional[pd.DataFrame]:
        """当前流式读取的数据块"""
        return self._current_streaming_chunk

    @current_streaming_chunk.setter
    def current_streaming_chunk(
        self,
        new_value: Optional[pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        self._current_streaming_chunk = new_value
        return self._current_streaming_chunk

    def file_exists(self, file_path: str) -> bool:
        """检查 S3 文件是否存在。

        Args:
            file_path: S3 文件路径

        Returns:
            bool: 文件存在返回 True，否则返回 False
        """
        return exists_s3_object(self.client, file_path)

    def _get_s3_file_names(self) -> list[str]:
        """获取当前 operator_step 对应的输出文件路径列表。

        Returns:
            list[str]: S3 文件路径列表
        """
        rtn: list[str] = []
        file_path = Path(self.output_s3_path.removeprefix("s3://")).joinpath(
            f"{self.operator_step:08}"
        )
        if self.batch_size is None:
            file_path = file_path.with_suffix(".jsonl")
            rtn.append("s3://" + str(file_path))
        else:
            for x, _ in list_s3_objects_detailed(
                self.client, "s3://" + str(file_path) + "/", True
            ):
                if x.endswith(".jsonl"):
                    rtn.append(x)
        return rtn

    def _read_file_line(
        self,
        s3_path: str,
        skip_bytes: int,
    ) -> Generator[tuple[str, int], None, None]:
        """从 S3 文件按行读取，支持断点续传。

        Args:
            s3_path: S3 文件路径
            skip_bytes: 跳过的前 N 字节（用于断点续传）

        Yields:
            tuple[str, int]: (解码后的行内容，下一个起始字节位置)
        """
        bucket_name, object_key = split_s3_path(s3_path)
        response = self.client.get_object(
            Bucket=bucket_name,
            Key=object_key,
            Range=f"bytes={skip_bytes}-",
        )
        streaming_body = response["Body"]
        counter = skip_bytes
        for line_bytes in streaming_body.iter_lines(keepends=True):
            counter += len(line_bytes)
            yield line_bytes.decode("utf-8"), counter

    def _read_results(self) -> Generator[dict, None, None]:
        """流式读取所有结果数据。

        根据 operator_step 决定读取输入数据还是中间结果：
        - step=0: 读取原始输入数据（self.s3_paths）
        - step>0: 读取前一个 operator 的输出文件

        Yields:
            dict: 解析后的 JSON 对象

        Note:
            支持 ResponseStreamingError 重试机制，网络超时时自动恢复读取。
        """
        if self.operator_step == 0:
            data_paths = self.s3_paths
        else:
            data_paths = self._get_s3_file_names()
        for x in data_paths:
            from_bytes = 0
            while True:
                try:
                    for line, next_bytes in self._read_file_line(x, from_bytes):
                        from_bytes = next_bytes
                        yield json.loads(line)

                    break
                except ResponseStreamingError:
                    self.logger.warning(
                        f"🔄 响应流超时：{x}, 从字节 {from_bytes} 恢复读取..."
                    )
            self.logger.info(f"📖 读取完成：{x} ({from_bytes} 字节)")

    def load_partition(self) -> pd.DataFrame:
        """加载当前分片的数据。

        根据 operator_step 和 batch_step 加载对应的数据分片。

        Returns:
            pd.DataFrame: 分片数据

        Note:
            - step=0: 从输入数据中按 batch_step 加载分片
            - step>0: 从前一个 operator 的输出文件中加载对应批次
        """
        if self.operator_step == 0:
            if not hasattr(self, "chunks"):
                self.chunks = self.iter_chunks()
                for part in range(self.batch_step - 1):
                    self.logger.info(f"⏭️ 跳过分片：{part + 1}")
                    next(self.chunks)
            self.logger.info(f"📦 读取分片：{self.batch_step}")
            return next(self.chunks)
        else:
            data_paths = self._get_s3_file_names()
            self.logger.info(f"📦 读取分片：{data_paths[self.batch_step - 1]}")
            assert data_paths[self.batch_step - 1].endswith(
                f"{self.batch_step:08}.jsonl"
            )
            rtn: list[dict] = []
            for line, _ in self._read_file_line(data_paths[self.batch_step - 1], 0):
                d = json.loads(line)
                rtn.append(d)
            return pd.DataFrame(rtn)

    def get_record_count(self) -> int:
        """获取总记录数。

        Returns:
            int: 数据总条数

        Note:
            需要遍历所有数据，大数据集可能较慢。
        """
        lines = 0
        for _ in self._read_results():
            lines += 1
        return lines

    def get_keys_from_dataframe(self) -> list[str]:
        """获取数据中的字段名列表。

        Returns:
            list[str]: 排序后的字段名列表

        Raises:
            Exception: 如果没有数据则抛出异常
        """
        for d in self._read_results():
            return sorted(list(d.keys()))
        raise Exception("no line found")

    def read(self, output_type: Literal["dataframe"] = "dataframe") -> pd.DataFrame:
        """读取全部数据。

        Args:
            output_type: 输出类型（目前只支持 dataframe）

        Returns:
            pd.DataFrame: 全部数据

        Note:
            如果 current_streaming_chunk 已设置，直接返回缓存的数据块。
        """
        if self._current_streaming_chunk is not None:
            return self._current_streaming_chunk

        data: list[dict] = []
        for d in self._read_results():
            data.append(d)

        return pd.DataFrame(data)

    def iter_chunks(self) -> Generator[pd.DataFrame, None, None]:
        """迭代读取数据块。

        Yields:
            pd.DataFrame: 每个数据块

        Note:
            - batch_size=None: 一次性返回全部数据
            - batch_size=N: 按 N 条记录分批返回
        """
        if self.batch_size is None:
            yield self.read()
            return

        data: list[dict] = []
        for d in self._read_results():
            data.append(d)
            if len(data) >= self.batch_size:
                yield pd.DataFrame(data)
                data = []

        if len(data):
            yield pd.DataFrame(data)

    def step(self):
        """推进到下一个 operator 步骤。

        Returns:
            S3JsonlStorage: 复制后的新实例（operator_step + 1）
        """
        self.operator_step += 1
        return copy.copy(self)

    def reset(self):
        """重置到初始状态。

        Returns:
            S3JsonlStorage: 自身（operator_step 重置为 -1）
        """
        self.operator_step = -1
        return self

    def write_file_path(self) -> str:
        """生成当前步骤的输出文件路径。

        Returns:
            str: S3 文件路径

        Note:
            - batch_size=None: s3://output_path/{operator_step+1:08}.jsonl
            - batch_size=N: s3://output_path/{operator_step+1:08}/{batch_step:08}.jsonl
        """
        file_path = Path(self.output_s3_path.removeprefix("s3://")).joinpath(
            f"{self.operator_step + 1:08}"
        )
        if self.batch_size is None:
            file_path = file_path.with_suffix(".jsonl")
        else:
            file_path = file_path.joinpath(f"{self.batch_step:08}").with_suffix(
                ".jsonl"
            )
        return "s3://" + str(file_path)

    def write(self, data: pd.DataFrame) -> str:
        """将数据写入 S3（JSONL 格式）。

        Args:
            data: 要写入的 DataFrame

        Returns:
            str: 写入的 S3 文件路径

        Note:
            - 自动清理无效 Unicode 字符
            - 使用 ensure_ascii=False 保留中文字符
        """
        def clean_surrogates(obj):
            """递归清理数据中的无效 Unicode 代理对字符"""
            if isinstance(obj, str):
                # 替换无效的 Unicode 代理对字符（如\\udc00）
                return obj.encode("utf-8", "replace").decode("utf-8")
            elif isinstance(obj, dict):
                return {k: clean_surrogates(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_surrogates(item) for item in obj]
            elif isinstance(obj, (int, float, bool)) or obj is None:
                # 数字、布尔值和 None 直接返回
                return obj
            else:
                # 其他类型（如自定义对象）尝试转为字符串处理
                try:
                    return clean_surrogates(str(obj))
                except:
                    # 如果转换失败，返回原对象或空字符串（根据需求选择）
                    return obj

        dataframe = data.map(clean_surrogates)

        content = ""
        for x in dataframe.to_dict(orient="records"):
            content += json.dumps(x, ensure_ascii=False) + "\n"

        file_path = self.write_file_path()
        bucket_name, object_key = split_s3_path(file_path)
        _ = self.client.put_object(
            Bucket=bucket_name,
            Key=object_key,
            Body=content,
        )

        self.logger.success(f"✅ 写入完成：{file_path} (JSONL)")

        return "s3://" + str(file_path)
