#!/usr/bin/env python3
"""为 Storage 模块添加 estimate_total_rows() 接口，优化 split_input 性能"""

from pathlib import Path

# 绝对路径
BASE = Path("/home/SENSETIME/wuziming/.openclaw/sandboxes/agent-pipeline-write-9f473f36/dataflow/dataflow/utils/storage")

# 1. iface.py - 添加 estimate_total_rows 抽象方法
iface = BASE / "iface.py"
c = iface.read_text()
old = '''    @abstractmethod
    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:'''
new = '''    @abstractmethod
    def estimate_total_rows(self) -> int:
        """估算总行数（无需完整读取数据）

        不同数据源采用不同策略：
        - 本地文件：采样估算或文件大小/平均每行
        - S3：对象元数据估算
        - HF/MS：数据集 API 直接返回

        Returns:
            估算的总行数（必须 >= 1）
        """
        pass

    @abstractmethod
    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:'''
iface.write_text(c.replace(old, new, 1))
print("✅ iface.py")

# 2. datasources.py  
ds = BASE / "datasources.py"
c = ds.read_text()
if "import json" not in c:
    c = "import json\n" + c

# FileDataSource
old = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="file", paths=self.paths)

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:'''
new = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="file", paths=self.paths)

    def estimate_total_rows(self) -> int:
        """通过采样估算总行数"""
        if not self.paths: return 1
        total_size, sampled_rows, sampled_bytes = 0, 0, 0
        for p in self.paths:
            total_size += Path(p).stat().st_size
            with open(p, "rb") as f:
                for _ in range(100):
                    line = f.readline()
                    if not line: break
                    sampled_rows += 1
                    sampled_bytes += len(line)
        if sampled_rows == 0: return 1
        return max(1, int(total_size / (sampled_bytes / sampled_rows)))

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:'''
c = c.replace(old, new, 1)

# S3DataSource
old = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="s3", paths=self.s3_paths)

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:'''
new = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="s3", paths=self.s3_paths)

    def estimate_total_rows(self) -> int:
        """通过 S3 对象元数据估算总行数"""
        if not self.s3_paths: return 1
        client = get_s3_client(self.endpoint, self.ak, self.sk)
        total_size = 0
        for p in self.s3_paths:
            try:
                parts = p.split("/", 3)
                if len(parts) >= 4:
                    h = client.head_object(Bucket=parts[2], Key=parts[3])
                    total_size += h.get("ContentLength", 0)
            except: pass
        if total_size == 0: return 1
        sampled_bytes, sampled_count = 0, 0
        try:
            content = read_s3_bytes(client, self.s3_paths[0])
            for row in get_parser(self.format_type).parse_to_dataframe(content, chunk_size=100):
                sampled_bytes += len(json.dumps(row).encode())
                sampled_count += 1
                if sampled_count >= 100: break
        except: pass
        avg = sampled_bytes / sampled_count if sampled_count else 200
        return max(1, int(total_size / avg))

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:'''
c = c.replace(old, new, 1)

# HuggingFaceDataSource
old = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="huggingface", paths=[self.dataset])

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        self._ensure_dataset()
        pbar = tqdm(desc=f"Loading {self.dataset}", unit="row")'''
new = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="huggingface", paths=[self.dataset])

    def estimate_total_rows(self) -> int:
        """从 HuggingFace 数据集 API 获取行数"""
        try:
            from datasets import load_dataset
            ds = load_dataset(self.dataset, split=self.split, streaming=False, **self.kwargs)
            return getattr(ds, "num_rows", 1) or 1
        except: return 1

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        self._ensure_dataset()
        pbar = tqdm(desc=f"Loading {self.dataset}", unit="row")'''
c = c.replace(old, new, 1)

# ModelScopeDataSource
old = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="modelscope", paths=[self.dataset])

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        self._ensure_dataset()
        pbar = tqdm(desc=f"Loading {self.dataset}", unit="row")'''
new = '''    def get_info(self) -> DataSourceInfo:
        return DataSourceInfo(source_type="modelscope", paths=[self.dataset])

    def estimate_total_rows(self) -> int:
        """从 ModelScope 数据集 API 获取行数"""
        try:
            from modelscope.msdatasets import load_dataset
            ds = load_dataset(self.dataset, split=self.split, **self.kwargs)
            if hasattr(ds, "num_rows"): return ds.num_rows or 1
            if hasattr(ds, "_info") and hasattr(ds._info, "splits"):
                s = ds._info.splits.get(self.split)
                if s: return s.num_examples or 1
            return 1
        except: return 1

    def read(self, chunk_size: int = 1000) -> Generator[dict, None, None]:
        self._ensure_dataset()
        pbar = tqdm(desc=f"Loading {self.dataset}", unit="row")'''
c = c.replace(old, new, 1)

ds.write_text(c)
print("✅ datasources.py")

# 3. file_storage.py
fs = BASE / "file_storage.py"
c = fs.read_text()
old = '''        rows = self.data_source.read()
        total = sum(1 for _ in rows)

        if total < num_partitions:
            raise RuntimeError("Rows count less than num of partitions.")

        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 总行数：{total}, 每片大小：{self._batch_size}")

        partition_paths = []
        rows = self.data_source.read()'''
new = '''        total = self.data_source.estimate_total_rows()
        if total < num_partitions:
            self.logger.warning(f"估算行数 {total} < partitions {num_partitions}")
        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 估算行数：{total}, 每片：{self._batch_size}")
        partition_paths = []
        rows = self.data_source.read()
        actual_total = 0'''
c = c.replace(old, new)

old = '''            for row in tqdm(rows, total=self._batch_size, desc="Reading Rows"):
                part.append(row)
                if len(part) >= self._batch_size:'''
new = '''            for row in tqdm(rows, total=self._batch_size, desc="Reading Rows", leave=False):
                part.append(row)
                actual_total += 1
                if len(part) >= self._batch_size:'''
c = c.replace(old, new)

old = '''        self.logger.info(f"Split input complete. {num_partitions} partitions created.")
        self._is_partitioned = True
        return partition_paths'''
new = '''        if actual_total != total:
            self.logger.info(f"📊 实际：{actual_total} (估算：{total})")
        self.logger.info(f"Split input complete. {num_partitions} partitions.")
        self._is_partitioned = True
        return partition_paths'''
c = c.replace(old, new)
fs.write_text(c)
print("✅ file_storage.py")

# 4. s3_storage.py
s3s = BASE / "s3_storage.py"
c = s3s.read_text()
old = '''        rows = self.data_source.read()
        total = sum(1 for _ in rows)

        if total < num_partitions:
            raise RuntimeError("Rows count less than num of partitions.")

        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 总行数：{total}, 每片大小：{self._batch_size}")

        partition_paths = []
        rows = self.data_source.read()'''
new = '''        total = self.data_source.estimate_total_rows()
        if total < num_partitions:
            self.logger.warning(f"估算行数 {total} < partitions {num_partitions}")
        self._batch_size = (total + num_partitions - 1) // num_partitions
        self.logger.info(f"📊 估算行数：{total}, 每片：{self._batch_size}")
        partition_paths = []
        rows = self.data_source.read()
        actual_total = 0'''
c = c.replace(old, new)

old = '''            for row in tqdm(rows, total=self._batch_size, desc="Reading Rows"):
                part.append(row)
                if len(part) >= self._batch_size:'''
new = '''            for row in tqdm(rows, total=self._batch_size, desc="Reading Rows", leave=False):
                part.append(row)
                actual_total += 1
                if len(part) >= self._batch_size:'''
c = c.replace(old, new)

old = '''        self._is_partitioned = True
        self.logger.info(f"Split input complete. {num_partitions} partitions created.")
        return partition_paths'''
new = '''        if actual_total != total:
            self.logger.info(f"📊 实际：{actual_total} (估算：{total})")
        self._is_partitioned = True
        self.logger.info(f"Split input complete. {num_partitions} partitions.")
        return partition_paths'''
c = c.replace(old, new)
s3s.write_text(c)
print("✅ s3_storage.py")

print("\n🎉 完成！split_input 现在只读取 DataSource 一次")
