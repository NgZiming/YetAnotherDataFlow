import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time

from pathlib import Path
from typing import Callable, Dict, Optional

from dataflow.logger import get_logger


class LRUCacheManager:
    """LRU 缓存管理器 - 管理硬盘上的缓存文件。

    核心设计：
    - _load_index() 上锁，返回索引，不解锁
    - _save_index() 写入索引并解锁
    - use() 中用 try-finally 确保锁的正确释放

    缓存目录结构：
    /tmp/.lru_cache/
    - .index.json           # 全局索引
    - .index.lock           # 文件锁
    - <md5_hash>.jsonl      # 缓存文件

    使用示例：
        cache_mgr = LRUCacheManager(max_size_gb=10)
        with cache_mgr.use("s3://bucket/file.jsonl", download_fn) as cache_path:
            df = pd.read_json(cache_path)
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        max_size_gb: float = 10.0,
        enable_cache: bool = True,
    ):
        """初始化 LRU 缓存管理器。

        Args:
            cache_dir: 缓存目录路径，默认使用 tempfile.gettempdir()/.lru_cache/
            max_size_gb: 缓存最大容量（GB）
            enable_cache: 是否启用缓存
        """
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path(tempfile.gettempdir()) / ".lru_cache"

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.max_size_bytes = int(max_size_gb * 1024**3)
        self.enable_cache = enable_cache

        self._index_path = self.cache_dir / ".index.json"
        self._lock_path = self.cache_dir / ".index.lock"

        self._hits = 0
        self._misses = 0

        self.logger = get_logger()

        self._lock_fd = None

    def _acquire_lock(self):
        """获取文件锁。"""
        assert self._lock_fd is None

        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        self._lock_fd = fd

        while True:
            try:
                # LOCK_EX = 排他锁，阻塞等待
                fcntl.flock(fd, fcntl.LOCK_EX)
                return True
            except (IOError, OSError):
                time.sleep(10)

    def _release_lock(self):
        """释放文件锁。"""
        assert self._lock_fd is not None
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
        except (IOError, OSError):
            pass
        finally:
            self._lock_fd = None

    def _load_index(self) -> Dict:
        """加载缓存索引（上锁，不解锁，由调用者负责）。

        Returns:
            索引字典
        """
        self._acquire_lock()
        if self._index_path.exists():
            try:
                with open(self._index_path) as f:
                    index = json.load(f)
                self.logger.info(f"📋 加载缓存索引，跟踪 {len(index)} 个文件")
                return index
            except Exception as e:
                self.logger.warning(f"⚠️ 无法加载缓存索引：{e}")
                return {}
        else:
            return {}

    def _save_index(self, index: Dict):
        """保存缓存索引并解锁。

        Args:
            index: 索引字典
        """
        try:
            with open(self._index_path, "w") as f:
                json.dump(index, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"❌ 保存索引失败：{e}")
        finally:
            self._release_lock()

    def _get_cache_file_path(self, source_path: str) -> Path:
        """获取缓存文件路径。

        Args:
            source_path: 源文件路径（如 S3 路径）

        Returns:
            缓存文件的本地路径
        """
        file_hash = hashlib.md5(source_path.encode()).hexdigest()
        suffix = Path(source_path).suffix or ".bin"
        return self.cache_dir / f"{file_hash}{suffix}"

    def _get_total_cache_size(self, index: Dict) -> int:
        """获取当前缓存总大小（字节）。

        Args:
            index: 索引字典

        Returns:
            缓存总大小（字节）
        """
        total_size = 0
        for info in index.values():
            cache_file = self.cache_dir / info["cache_file"]
            if cache_file.exists():
                total_size += info.get("size", cache_file.stat().st_size)
        return total_size

    def _evict_lru_files(self, index: Dict):
        """淘汰 LRU 文件（只淘汰无引用的文件）。

        Args:
            index: 索引字典（会被修改）
        """
        if self._get_total_cache_size(index) < self.max_size_bytes:
            return

        self.logger.info("🗑️ 缓存超限，开始清理...")

        evictable = [
            (source_path, info)
            for source_path, info in index.items()
            if info.get("ref_count", 0) == 0 and info.get("status") == "cached"
        ]
        evictable.sort(key=lambda x: x[1].get("last_access", 0))

        for source_path, info in evictable:
            if self._get_total_cache_size(index) < self.max_size_bytes:
                break

            cache_file = self.cache_dir / info["cache_file"]
            if cache_file.exists():
                try:
                    cache_file.unlink()
                    del index[source_path]
                    self.logger.info(
                        f"🗑️ 已淘汰：{info['cache_file']} "
                        f"(ref_count={info.get('ref_count', 0)})"
                    )
                except Exception as e:
                    self.logger.error(f"❌ 无法删除缓存文件：{e}")

    def _wait_for_file(self, cache_path: Path):
        """等待缓存文件出现（无限等待，直到文件出现）。

        Args:
            cache_path: 缓存文件路径

        Returns:
            True 当文件出现时返回
        """
        while not cache_path.exists():
            time.sleep(10)
        return True

    @contextlib.contextmanager
    def use(
        self,
        source_path: str,
        download_fn: Optional[Callable] = None,
    ):
        """使用缓存文件（上下文管理器）。

        流程（用 try-finally 确保锁的正确释放）：
        1. 如果是本地文件路径，直接返回
        2. _load_index() 上锁，检查状态，用 try-finally 包裹：
           - cached: ref_count++，_save_index() 解锁
           - downloading: _save_index() 解锁→等待→_load_index() 上锁→ref_count++→_save_index() 解锁
           - 不存在：标记 downloading，_save_index() 解锁→下载→_load_index() 上锁→标记 cached→_save_index() 解锁
        3. 退出时：_load_index() 上锁→ref_count--→_save_index() 解锁

        自动管理引用计数：进入时增加，退出时减少。

        Args:
            source_path: 源文件路径（如 S3 路径）或本地文件路径
            download_fn: 下载函数，接收目标文件路径作为参数

        Yields:
            缓存文件的本地路径

        Example:
            with cache_mgr.use("s3://bucket/file.jsonl", download_fn) as cache_path:
                df = pd.read_json(cache_path)
        """
        # 1. 本地文件路径：直接返回，跳过所有缓存逻辑
        if os.path.exists(source_path):
            yield source_path
            return

        if not self.enable_cache:
            tmp = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=Path(source_path).suffix,
            )
            tmp.close()
            if download_fn:
                download_fn(tmp.name)
            try:
                yield tmp.name
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
            return

        cache_path = self._get_cache_file_path(source_path)
        managed = False
        wait = False

        # 2. _load_index() 上锁，检查状态
        index = self._load_index()
        try:
            if source_path in index:
                managed = True
                if index[source_path].get("status") == "cached":
                    if not cache_path.exists():
                        raise Exception("file not exists")
                    self._hits += 1
                    index[source_path]["last_access"] = time.time()
                    index[source_path]["ref_count"] = (
                        index[source_path].get("ref_count", 0) + 1
                    )
                    self.logger.info(f"✅ 缓存命中：{cache_path.name}")
                elif index[source_path].get("status") == "downloading":
                    wait = True
                    self.logger.info(f"⏳ 等待其他进程下载：{cache_path.name}")
                else:
                    raise Exception("unknow status")
            else:
                self._evict_lru_files(index)

                self._misses += 1
                self.logger.info(f"📥 缓存未命中，正在下载：{source_path}")

                index[source_path] = {
                    "cache_file": cache_path.name,
                    "status": "downloading",
                    "last_access": time.time(),
                    "ref_count": 1,
                    "size": 0,
                }
        finally:
            self._save_index(index)  # 解锁

        if wait:
            self._wait_for_file(cache_path)
            # _load_index() 重新上锁
            index = self._load_index()
            try:
                self._hits += 1
                index[source_path]["last_access"] = time.time()
                index[source_path]["ref_count"] = (
                    index[source_path].get("ref_count", 0) + 1
                )
                self.logger.info(f"✅ 下载完成，缓存命中：{cache_path.name}")
            finally:
                self._save_index(index)  # 解锁，等待下载完成

        # 6. 如果需要下载，执行下载
        if not managed:
            try:
                if not download_fn:
                    raise ValueError(f"缓存未命中且未提供下载函数：{source_path}")

                # 先下载到临时文件
                temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
                download_fn(str(temp_path))
                self.logger.info(f"✅ 下载完成，正在提交：{cache_path.name}")

                # _load_index() 上锁，更新为 cached
                index = self._load_index()
                try:
                    # 原子重命名
                    temp_path.rename(cache_path)
                    self.logger.info(f"✅ 文件已提交：{cache_path.name}")
                    file_size = cache_path.stat().st_size
                    index[source_path]["status"] = "cached"
                    index[source_path]["size"] = file_size
                finally:
                    self._save_index(index)  # 解锁
            except Exception as e:
                self.logger.error(f"❌ 下载失败：{e}")
                # 清理临时文件和正式文件
                temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
                if temp_path.exists():
                    temp_path.unlink()
                if cache_path.exists():
                    cache_path.unlink()

                # _load_index() 上锁，清理索引中的下载标记
                index = self._load_index()
                try:
                    if source_path in index:
                        del index[source_path]
                finally:
                    self._save_index(index)  # 解锁
                raise

        # 7. 使用缓存文件
        try:
            yield str(cache_path)
        finally:
            # 退出时：_load_index() 上锁→ref_count--→_save_index() 解锁
            index = self._load_index()
            try:
                if source_path in index:
                    index[source_path]["ref_count"] -= 1
            finally:
                self._save_index(index)
