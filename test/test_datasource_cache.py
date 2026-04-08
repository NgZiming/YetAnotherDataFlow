#!/usr/bin/env python3
"""DataSource 缓存集成测试。

注意：Cache 只为 S3DataSource 使用，FileDataSource 不需要缓存。
"""

import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataflow.utils.storage.data_cache import LRUCacheManager
from dataflow.utils.storage.data_parser import get_parser


def test_s3_datasource_with_cache():
    """测试：S3DataSource 使用缓存。"""
    print("=" * 60)
    print("测试：S3DataSource 使用缓存")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp(prefix="lru_test_")
    cache_dir = Path(temp_dir) / ".lru_cache"

    try:
        # 模拟 S3 数据
        data_file = Path(temp_dir) / "mock_s3_data.jsonl"
        with open(data_file, "w") as f:
            for i in range(10):
                f.write(json.dumps({"id": i, "name": f"s3_item_{i}"}) + "\n")

        cache_mgr = LRUCacheManager(
            cache_dir=cache_dir,
            max_size_gb=1.0,
            enable_cache=True,
        )

        parser = get_parser("jsonl")

        def download_fn(dest):
            with open(data_file, "rb") as f:
                with open(dest, "wb") as out:
                    out.write(f.read())

        s3_path = "s3://bucket/mock_data.jsonl"

        # 第一次访问（缓存未命中）
        with cache_mgr.use(s3_path, download_fn) as cache_path:
            results1 = list(parser.parse_to_dataframe(cache_path, chunk_size=1000))

        assert len(results1) == 10, f"应该有 10 条数据，实际 {len(results1)}"
        print(f"✅ 第一次访问：下载了 {len(results1)} 条数据")

        # 第二次访问（缓存命中）
        with cache_mgr.use(s3_path, download_fn) as cache_path:
            results2 = list(parser.parse_to_dataframe(cache_path, chunk_size=1000))

        assert len(results2) == 10, f"应该有 10 条数据，实际 {len(results2)}"
        assert cache_mgr._hits == 1, f"应该有 1 次缓存命中，实际 {cache_mgr._hits}"

        print(f"✅ 第二次访问：缓存命中，读取了 {len(results2)} 条数据")
        print(f"\n📊 缓存统计:")
        print(f"   命中次数：{cache_mgr._hits}")
        print(f"   未命中次数：{cache_mgr._misses}")

        print("\n✅ 测试通过")

    finally:
        shutil.rmtree(temp_dir)


def test_file_datasource_no_cache():
    """测试：FileDataSource 不使用缓存。"""
    print("\n" + "=" * 60)
    print("测试：FileDataSource（无缓存）")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp(prefix="lru_test_")
    try:
        # 创建本地文件
        local_file = Path(temp_dir) / "test.jsonl"
        with open(local_file, "w") as f:
            for i in range(10):
                f.write(json.dumps({"id": i, "name": f"item_{i}"}) + "\n")

        parser = get_parser("jsonl")

        # 直接读取本地文件（不使用缓存）
        results = list(parser.parse_to_dataframe(str(local_file), chunk_size=1000))

        assert len(results) == 10, f"应该有 10 条数据，实际 {len(results)}"
        print(f"✅ 读取了 {len(results)} 条数据")
        print("✅ 测试通过（本地文件不经过缓存）")

    finally:
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    print("\n🧪 开始 DataSource 缓存测试\n")

    test_s3_datasource_with_cache()
    test_file_datasource_no_cache()

    print("\n" + "=" * 60)
    print("✅ 所有 DataSource 测试完成")
    print("=" * 60 + "\n")
