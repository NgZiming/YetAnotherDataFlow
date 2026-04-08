#!/usr/bin/env python3
"""LRUCacheManager 简单测试脚本（不依赖 pytest）。

快速验证缓存功能是否正常。
"""

import os
import shutil
import sys
import tempfile
import multiprocessing
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataflow.utils.storage.data_cache import LRUCacheManager


def test_basic_cache():
    """测试：基本缓存功能。"""
    print("=" * 60)
    print("测试：基本缓存功能")
    print("=" * 60)

    temp_cache_dir = tempfile.mkdtemp(prefix="lru_test_")
    try:
        cache_mgr = LRUCacheManager(
            cache_dir=temp_cache_dir,
            max_size_gb=1.0,
            enable_cache=True,
        )

        def download_fn(dest):
            with open(dest, "w") as f:
                f.write("test content " * 100)

        with cache_mgr.use("s3://bucket/file1.jsonl", download_fn):
            pass

        index = cache_mgr._load_index()
        assert "s3://bucket/file1.jsonl" in index
        cache_mgr._save_index(index)

        with cache_mgr.use("s3://bucket/file1.jsonl", download_fn):
            pass

        assert cache_mgr._hits == 1, "应该有 1 次命中"
        assert cache_mgr._misses == 1, "应该有 1 次未命中"
        print("✅ 测试通过")

    finally:
        shutil.rmtree(temp_cache_dir)


def worker_download(cache_dir, source_path, download_count_file, result_file):
    """多进程下载的工作函数。"""
    cache_mgr = LRUCacheManager(
        cache_dir=cache_dir,
        max_size_gb=1.0,
        enable_cache=True,
    )

    def slow_download_fn(dest):
        with open(download_count_file, "a") as f:
            f.write("download\n")
        time.sleep(1)
        with open(dest, "w") as f:
            f.write("downloaded content")

    try:
        with cache_mgr.use(source_path, slow_download_fn) as cache_path:
            with open(cache_path) as f:
                f.read()
            with open(result_file, "a") as f:
                f.write("success\n")
    except Exception as e:
        with open(result_file, "a") as f:
            f.write(f"error:{e}\n")


def test_concurrent_download():
    """测试：并发下载（多进程）。"""
    print("\n" + "=" * 60)
    print("测试：并发下载（多进程）")
    print("=" * 60)

    temp_cache_dir = tempfile.mkdtemp(prefix="lru_test_")
    download_count_file = Path(temp_cache_dir) / "download_count.txt"
    result_file = Path(temp_cache_dir) / "results.txt"

    download_count_file.touch()
    result_file.touch()

    try:
        # 启动 3 个进程
        processes = []
        for i in range(3):
            p = multiprocessing.Process(
                target=worker_download,
                args=(
                    temp_cache_dir,
                    "s3://bucket/slow_file.jsonl",
                    download_count_file,
                    result_file,
                ),
            )
            processes.append(p)

        start_time = time.time()
        for p in processes:
            p.start()

        for p in processes:
            p.join()

        elapsed = time.time() - start_time

        with open(download_count_file) as f:
            download_count = len(
                [line for line in f.readlines() if line.strip() == "download"]
            )

        with open(result_file) as f:
            results = [
                line.strip() for line in f.readlines() if line.strip() == "success"
            ]

        print(f"\n   总耗时：{elapsed:.2f}秒")
        print(f"   实际下载次数：{download_count}")
        print(f"   成功结果数：{len(results)}")

        assert download_count == 1, f"应该只下载 1 次，实际下载了 {download_count} 次"
        assert len(results) == 3, f"应该有 3 个成功结果，实际有 {len(results)} 个"
        assert elapsed < 25, f"耗时应该小于 25 秒，实际 {elapsed:.2f} 秒"
        print("✅ 测试通过")

    finally:
        shutil.rmtree(temp_cache_dir)


def test_reference_count():
    """测试：引用计数管理。"""
    print("\n" + "=" * 60)
    print("测试：引用计数管理")
    print("=" * 60)

    temp_cache_dir = tempfile.mkdtemp(prefix="lru_test_")
    try:
        cache_mgr = LRUCacheManager(
            cache_dir=temp_cache_dir,
            max_size_gb=1.0,
            enable_cache=True,
        )

        def download_fn(dest):
            with open(dest, "w") as f:
                f.write("test content")

        print("\n1. 第一次使用")
        with cache_mgr.use("s3://bucket/file.jsonl", download_fn):
            index = cache_mgr._load_index()
            print(
                f"   进入时 ref_count: {index['s3://bucket/file.jsonl']['ref_count']}"
            )
            assert index["s3://bucket/file.jsonl"]["ref_count"] == 1
            cache_mgr._save_index(index)

        index = cache_mgr._load_index()
        print(f"   退出后 ref_count: {index['s3://bucket/file.jsonl']['ref_count']}")
        assert index["s3://bucket/file.jsonl"]["ref_count"] == 0
        cache_mgr._save_index(index)

        print("\n2. 第二次使用（嵌套）")
        with cache_mgr.use("s3://bucket/file.jsonl", download_fn):
            index = cache_mgr._load_index()
            print(
                f"   进入时 ref_count: {index['s3://bucket/file.jsonl']['ref_count']}"
            )
            assert index["s3://bucket/file.jsonl"]["ref_count"] == 1
            cache_mgr._save_index(index)

            with cache_mgr.use("s3://bucket/file.jsonl", download_fn):
                index = cache_mgr._load_index()
                print(
                    f"   嵌套时 ref_count: {index['s3://bucket/file.jsonl']['ref_count']}"
                )
                assert index["s3://bucket/file.jsonl"]["ref_count"] == 2
                cache_mgr._save_index(index)

            index = cache_mgr._load_index()
            print(
                f"   退出嵌套后 ref_count: {index['s3://bucket/file.jsonl']['ref_count']}"
            )
            assert index["s3://bucket/file.jsonl"]["ref_count"] == 1
            cache_mgr._save_index(index)

        index = cache_mgr._load_index()
        print(f"   最终 ref_count: {index['s3://bucket/file.jsonl']['ref_count']}")
        assert index["s3://bucket/file.jsonl"]["ref_count"] == 0
        cache_mgr._save_index(index)
        print("✅ 测试通过")

    finally:
        shutil.rmtree(temp_cache_dir)


def test_download_failure():
    """测试：下载失败处理。"""
    print("\n" + "=" * 60)
    print("测试：下载失败处理")
    print("=" * 60)

    temp_cache_dir = tempfile.mkdtemp(prefix="lru_test_")
    try:
        cache_mgr = LRUCacheManager(
            cache_dir=temp_cache_dir,
            max_size_gb=1.0,
            enable_cache=True,
        )

        def failing_download_fn(dest):
            raise Exception("下载失败")

        try:
            with cache_mgr.use("s3://bucket/file.jsonl", failing_download_fn):
                pass
            assert False, "应该抛出异常"
        except Exception as e:
            assert "下载失败" in str(e)

        cache_path = cache_mgr._get_cache_file_path("s3://bucket/file.jsonl")
        assert not cache_path.exists(), "下载失败后缓存文件应该被删除"

        index = cache_mgr._load_index()
        assert "s3://bucket/file.jsonl" not in index
        cache_mgr._save_index(index)
        print("✅ 测试通过")

    finally:
        shutil.rmtree(temp_cache_dir)


if __name__ == "__main__":
    print("\n🧪 开始 LRUCacheManager 测试\n")

    test_basic_cache()
    test_concurrent_download()
    test_reference_count()
    test_download_failure()

    print("\n" + "=" * 60)
    print("✅ 所有测试完成")
    print("=" * 60 + "\n")
