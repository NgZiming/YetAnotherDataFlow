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

__re_s3_path = re.compile("^s3a?://([^/]+)(?:/(.*))?$")


def split_s3_path(path: str):
    "split bucket and key from path"
    m = __re_s3_path.match(path)
    if m is None:
        return "", ""
    return m.group(1), (m.group(2) or "")


def list_s3_objects_detailed(
    client, path: str, recursive=False, is_prefix=False, limit=0
):
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
    try:
        bucket, key = split_s3_path(s3_path)
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise e


def read_s3_bytes(client, s3_path: str) -> bytes:
    bucket_name, object_key = split_s3_path(s3_path)
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    streaming_body = response["Body"]
    return streaming_body.read()


class MediaStorage(ABC):
    @abstractmethod
    def read_media_bytes(self, media_path: str) -> bytes:
        raise NotImplementedError


class S3MediaStorage(MediaStorage):
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
        return read_s3_bytes(get_s3_client(self.endpoint, self.ak, self.sk), media_path)


class FileMediaStorage(MediaStorage):
    def __init__(self) -> None:
        pass

    def read_media_bytes(self, media_path: str) -> bytes:
        return open(media_path, "rb").read()


class S3JsonlStorage(DataFlowStorage):
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

        self.batch_step = 0
        self.batch_size = None
        self.operator_step = -1
        self.logger = get_logger()

        self._current_streaming_chunk: Optional[pd.DataFrame] = None

    def _get_s3_file_names(self) -> list[str]:
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
                        f"response stream timeout for file {x}, resume read from bytes: {from_bytes}."
                    )
            self.logger.info(f"read {from_bytes} bytes from file {x}.")

    def load_partition(self) -> pd.DataFrame:
        if self.operator_step == 0:
            if not hasattr(self, "chunks"):
                self.chunks = self.iter_chunks()
            return next(self.chunks)
        else:
            data_paths = self._get_s3_file_names()
            self.logger.info(f"read partition: {data_paths[self.batch_step - 1]}")
            assert data_paths[self.batch_step - 1].endswith(
                f"{self.batch_step:08}.jsonl"
            )
            rtn: list[dict] = []
            for line, _ in self._read_file_line(data_paths[self.batch_step - 1], 0):
                d = json.loads(line)
                rtn.append(d)
            return pd.DataFrame(rtn)

    def get_record_count(self) -> int:
        lines = 0
        for _ in self._read_results():
            lines += 1
        return lines

    def get_keys_from_dataframe(self) -> list[str]:
        for d in self._read_results():
            return sorted(list(d.keys()))
        raise Exception("no line found")

    def read(self, output_type: Literal["dataframe"] = "dataframe") -> pd.DataFrame:
        if self._current_streaming_chunk is not None:
            return self._current_streaming_chunk

        data: list[dict] = []
        for d in self._read_results():
            data.append(d)

        return pd.DataFrame(data)

    def iter_chunks(self) -> Generator[pd.DataFrame, None, None]:
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
        self.operator_step += 1
        return copy.copy(self)

    def reset(self):
        self.operator_step = -1
        return self

    def write(self, data: pd.DataFrame) -> str:
        def clean_surrogates(obj):
            """递归清理数据中的无效Unicode代理对字符"""
            if isinstance(obj, str):
                # 替换无效的Unicode代理对字符（如\udc00）
                return obj.encode("utf-8", "replace").decode("utf-8")
            elif isinstance(obj, dict):
                return {k: clean_surrogates(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_surrogates(item) for item in obj]
            elif isinstance(obj, (int, float, bool)) or obj is None:
                # 数字、布尔值和None直接返回
                return obj
            else:
                # 其他类型（如自定义对象）尝试转为字符串处理
                try:
                    return clean_surrogates(str(obj))
                except:
                    # 如果转换失败，返回原对象或空字符串（根据需求选择）
                    return obj

        dataframe = data.map(clean_surrogates)

        file_path = Path(self.output_s3_path.removeprefix("s3://")).joinpath(
            f"{self.operator_step + 1:08}"
        )
        if self.batch_size is None:
            file_path = file_path.with_suffix(".jsonl")
        else:
            file_path = file_path.joinpath(f"{self.batch_step:08}").with_suffix(
                ".jsonl"
            )

        content = ""
        for x in dataframe.to_dict(orient="records"):
            content += json.dumps(x, ensure_ascii=False) + "\n"

        bucket_name, object_key = split_s3_path("s3://" + str(file_path))
        _ = self.client.put_object(
            Bucket=bucket_name,
            Key=object_key,
            Body=content,
        )

        self.logger.success(f"Writing data to s3://{str(file_path)} with type jsonl")

        return "s3://" + str(file_path)
