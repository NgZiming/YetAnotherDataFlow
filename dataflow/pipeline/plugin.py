from abc import ABC, abstractmethod
from pathlib import Path

from dataflow.utils.s3_plugin import (
    exists_s3_object,
    get_s3_client,
    read_s3_bytes,
    split_s3_path,
)


class CacheStorage(ABC):
    @abstractmethod
    def record_steps(self, operator_step: int, batch_step: int, batch_size: int):
        raise NotImplementedError

    @abstractmethod
    def get_steps(self) -> tuple[int, int, int]:
        raise NotImplementedError


class FileCacheStorage(CacheStorage):
    def __init__(self, cache_file: str) -> None:
        self.cache_file = cache_file

    def record_steps(self, operator_step: int, batch_step: int, batch_size: int):
        with open(self.cache_file, "w") as f:
            f.write(f"{operator_step},{batch_step},{batch_size}")

    def get_steps(self) -> tuple[int, int, int]:
        if not Path(self.cache_file).exists():
            return 0, 0, 0

        with open(self.cache_file, "r") as f:
            operator_step, batch_step, batch_size = f.readline().split(",")
            return int(operator_step), int(batch_step), int(batch_size)


class S3CacheStorage(CacheStorage):
    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
        cache_file: str,
    ) -> None:
        super().__init__()
        self.client = get_s3_client(endpoint, ak, sk)
        self.cache_file = cache_file

    def record_steps(self, operator_step: int, batch_step: int, batch_size: int):
        bucket_name, object_key = split_s3_path(self.cache_file)
        _ = self.client.put_object(
            Bucket=bucket_name,
            Key=object_key,
            Body=f"{operator_step},{batch_step},{batch_size}",
        )

    def get_steps(self) -> tuple[int, int, int]:
        if not exists_s3_object(self.client, self.cache_file):
            return 0, 0, 0

        operator_step, batch_step, batch_size = (
            read_s3_bytes(self.client, self.cache_file).decode().split(",")
        )
        return int(operator_step), int(batch_step), int(batch_size)
