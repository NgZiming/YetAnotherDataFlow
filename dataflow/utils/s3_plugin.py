"""
S3 存储插件模块。

提供 S3 对象存储的通用工具函数，支持：
- S3 路径解析和对象列表
- 媒体文件读取（S3/本地）
- JSONL 格式的分布式存储（支持断点续传、分批处理）

主要函数：
    - split_s3_path: 解析 S3 路径为 (bucket, key)
    - list_s3_objects_detailed: 列出 S3 对象（支持分页）
    - get_s3_client: 创建配置好的 S3 客户端
    - exists_s3_object: 检查对象是否存在
    - is_s3_object_empty: 检查对象是否为空
    - read_s3_bytes: 读取对象内容
    - put_s3_object: 上传对象

依赖：
    - boto3: S3 SDK
    - botocore: 配置和异常处理
"""

import re

import boto3

from botocore.client import Config
from botocore.exceptions import ClientError

from dataflow.logger import get_logger

logger = get_logger()


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


def is_s3_object_empty(client, path: str) -> bool | None:
    """检查 S3 对象是否为空。

    Args:
        client: boto3 S3 client 对象
        path: S3 路径

    Returns:
        True 表示为空，False 表示不为空，None 表示检查失败

    Note:
        head_object 比 get_object 更高效，不下载文件内容
    """
    try:
        bucket_name, object_key = split_s3_path(path)
        response = client.head_object(Bucket=bucket_name, Key=object_key)
        size = response["ContentLength"]
        return size == 0
    except ClientError as e:
        logger.warning(f"⚠️ 检查 S3 对象失败 '{path}': {e}")
        return None


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


def put_s3_object(client, s3_path: str, bytes_data: bytes):
    """上传对象到 S3。

    Args:
        client: boto3 S3 client 对象
        s3_path: S3 路径（s3://bucket/key）
        bytes_data: 要上传的字节数据

    Note:
        如果对象已存在会被覆盖。
    """
    bucket_name, object_key = split_s3_path(s3_path)
    client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=bytes_data,
    )
