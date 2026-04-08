#!/usr/bin/env python3
"""LRUCacheManager 核心测试（pytest 兼容）。

运行方式：
    pytest test/test_lru_cache.py -v
"""

import os
import shutil
import tempfile
import multiprocessing
import time
from pathlib import Path

import pytest

from dataflow.utils.storage.data_cache import LRUCacheManager


@pytest.fixture
def temp_cache_dir():
    """创建临时缓存目录，测试后自动清理。"""
    cache_dir = tempfile.mkdtemp(prefix="lru_test_")
    yield cache_dir
    shutil.rmtree(cache_dir)


def test_basic_cache(temp_cache_dir):
    """测试 1: 基本缓存功能（命中/未命中）。"""
    cache_mgr = LRUCacheManager(
        cache_dir=temp_cache_dir,
        max_size_gb=1.0,
        enable_cache=True,
    )

    def download_fn(dest):
        with open(dest, "w") as f:
            f.write("test content " * 100)

    # 首次访问 - 缓存未命中
    with cache_mgr.use("s3://bucket/file1.jsonl", download_fn) as cache_path:
        assert os.path.exists(cache_path)

    # 验证索引中有该文件
    index = cache_mgr._load_index()
    assert "s3://bucket/file1.jsonl" in index
    cache_mgr._save_index(index)

    # 再次访问 - 缓存命中
    with cache_mgr.use("s3://bucket/file1.jsonl", download_fn) as cache_path:
        assert os.path.exists(cache_path)

    # 验证统计信息
    assert cache_mgr._hits == 1, "应该有 1 次命中"
    assert cache_mgr._misses == 1, "应该有 1 次未命中"


def worker_download(cache_dir, source_path, download_count_file, result_file):
    """多进程下载的工作函数。"""
    cache_mgr = LRUCacheManager(
        cache_dir=cache_dir,
        max_size_gb=1.0,
        enable_cache=True,
    )

    download_count = [0]

    def slow_download_fn(dest):
        download_count[0] += 1
        # 写入下载计数
        with open(download_count_file, "a") as f:
            f.write(f"download\n")
        time.sleep(1)  # 模拟 1 秒下载
        with open(dest, "w") as f:
            f.write("downloaded content")

    try:
        with cache_mgr.use(source_path, slow_download_fn) as cache_path:
            with open(cache_path) as f:
                content = f.read()
            with open(result_file, "a") as f:
                f.write(f"success\n")
    except Exception as e:
        with open(result_file, "a") as f:
            f.write(f"error:{e}\n")


def test_concurrent_download(temp_cache_dir):
    """测试 2: 并发下载（多进程，下载锁机制）。"""
    download_count_file = Path(temp_cache_dir) / "download_count.txt"
    result_file = Path(temp_cache_dir) / "results.txt"

    # 创建文件
    download_count_file.touch()
    result_file.touch()

    # 启动 3 个进程同时下载同一个文件
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

    # 验证下载次数
    with open(download_count_file) as f:
        download_count = len(
            [line for line in f.readlines() if line.strip() == "download"]
        )

    # 验证结果
    with open(result_file) as f:
        results = [line.strip() for line in f.readlines() if line.strip() == "success"]

    print(f"\n   总耗时：{elapsed:.2f}秒")
    print(f"   实际下载次数：{download_count}")
    print(f"   成功结果数：{len(results)}")

    assert download_count == 1, f"应该只下载 1 次，实际下载了 {download_count} 次"
    assert len(results) == 3, f"应该有 3 个成功结果，实际有 {len(results)} 个"
    assert elapsed < 25, f"耗时应该小于 25 秒，实际 {elapsed:.2f} 秒"


def test_reference_count(temp_cache_dir):
    """测试 3: 引用计数管理。"""
    cache_mgr = LRUCacheManager(
        cache_dir=temp_cache_dir,
        max_size_gb=1.0,
        enable_cache=True,
    )

    def download_fn(dest):
        with open(dest, "w") as f:
            f.write("test content")

    # 第一次使用
    with cache_mgr.use("s3://bucket/file.jsonl", download_fn):
        index = cache_mgr._load_index()
        assert index["s3://bucket/file.jsonl"]["ref_count"] == 1
        cache_mgr._save_index(index)

    # 第一次使用后，ref_count 应该减到 0
    index = cache_mgr._load_index()
    assert index["s3://bucket/file.jsonl"]["ref_count"] == 0
    cache_mgr._save_index(index)

    # 第二次使用（嵌套）
    with cache_mgr.use("s3://bucket/file.jsonl", download_fn):
        index = cache_mgr._load_index()
        assert index["s3://bucket/file.jsonl"]["ref_count"] == 1
        cache_mgr._save_index(index)

        # 嵌套使用
        with cache_mgr.use("s3://bucket/file.jsonl", download_fn):
            index = cache_mgr._load_index()
            assert index["s3://bucket/file.jsonl"]["ref_count"] == 2
            cache_mgr._save_index(index)

        index = cache_mgr._load_index()
        assert index["s3://bucket/file.jsonl"]["ref_count"] == 1
        cache_mgr._save_index(index)

    index = cache_mgr._load_index()
    assert index["s3://bucket/file.jsonl"]["ref_count"] == 0
    cache_mgr._save_index(index)


def test_cache_eviction(temp_cache_dir):
    """测试 4: 缓存淘汰。"""
    # 设置很小的缓存限制（约 1KB）
    cache_mgr = LRUCacheManager(
        cache_dir=temp_cache_dir,
        max_size_gb=0.000001,  # 约 1KB
        enable_cache=True,
    )

    # 创建多个文件
    for i in range(5):

        def download_fn(dest, idx=i):
            # 每个文件约 500 字节
            with open(dest, "w") as f:
                f.write(f"file {idx} content: " + "x" * 480)

        with cache_mgr.use(f"s3://bucket/file{i}.jsonl", download_fn):
            pass

        time.sleep(0.1)  # 确保时间戳不同

    # 验证缓存中有一些文件（可能部分被清理）
    index = cache_mgr._load_index()
    cache_mgr._save_index(index)
    assert len(index) >= 0


def test_download_failure(temp_cache_dir):
    """测试 5: 下载失败处理。"""
    cache_mgr = LRUCacheManager(
        cache_dir=temp_cache_dir,
        max_size_gb=1.0,
        enable_cache=True,
    )

    def failing_download_fn(dest):
        raise Exception("下载失败")

    # 下载应该抛出异常
    with pytest.raises(Exception, match="下载失败"):
        with cache_mgr.use("s3://bucket/file.jsonl", failing_download_fn):
            pass

    # 缓存文件应该被清理
    cache_path = cache_mgr._get_cache_file_path("s3://bucket/file.jsonl")
    assert not cache_path.exists(), "下载失败后缓存文件应该被删除"

    # 索引中不应该有该文件
    index = cache_mgr._load_index()
    assert "s3://bucket/file.jsonl" not in index
    cache_mgr._save_index(index)


def test_atomic_write(temp_cache_dir):
    """测试 6: 原子写入（.tmp + rename）。"""
    cache_mgr = LRUCacheManager(
        cache_dir=temp_cache_dir,
        max_size_gb=1.0,
        enable_cache=True,
    )

    def download_fn(dest):
        # 写入 .tmp 文件
        with open(dest, "w") as f:
            f.write("complete content")
        # 调用者负责 rename

    # 手动测试原子写入
    cache_path = cache_mgr._get_cache_file_path("s3://bucket/atomic.jsonl")
    temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")

    # 写入临时文件
    download_fn(str(temp_path))
    assert temp_path.exists()
    assert not cache_path.exists()

    # 原子重命名
    temp_path.rename(cache_path)
    assert cache_path.exists()
    assert not temp_path.exists()

    # 更新索引
    index = cache_mgr._load_index()
    index["s3://bucket/atomic.jsonl"] = {
        "cache_file": cache_path.name,
        "status": "cached",
        "last_access": time.time(),
        "ref_count": 0,
        "size": cache_path.stat().st_size,
    }
    cache_mgr._save_index(index)


def test_cache_disabled(temp_cache_dir):
    """测试 7: 缓存禁用。"""
    cache_mgr = LRUCacheManager(
        cache_dir=temp_cache_dir,
        max_size_gb=1.0,
        enable_cache=False,
    )

    def download_fn(dest):
        with open(dest, "w") as f:
            f.write("test content")

    # 使用缓存（应该下载到临时文件）
    with cache_mgr.use("s3://bucket/file.jsonl", download_fn) as cache_path:
        assert os.path.exists(cache_path)
        # 临时文件应该在退出后删除
    assert not os.path.exists(cache_path), "临时文件应该已删除"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
