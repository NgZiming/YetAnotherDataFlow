#!/usr/bin/env python3
"""修复 FileDataSource.estimate_total_rows，使用 DataParser 采样"""

from pathlib import Path

BASE = Path("/home/SENSETIME/wuziming/.openclaw/sandboxes/agent-pipeline-write-9f473f36/dataflow/dataflow/utils/storage")

ds = BASE / "datasources.py"
c = ds.read_text()

# 修改 FileDataSource.estimate_total_rows
old = '''    def estimate_total_rows(self) -> int:
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
        return max(1, int(total_size / (sampled_bytes / sampled_rows)))'''

new = '''    def estimate_total_rows(self) -> int:
        """通过采样估算总行数

        使用 DataParser 解析前 N 行，计算平均每行字节数，
        然后用总文件大小/平均每行字节数估算。
        这样可以正确处理 CSV、Parquet 等不同格式。
        """
        if not self.paths: return 1

        total_size = sum(Path(p).stat().st_size for p in self.paths)
        if total_size == 0: return 1

        # 使用 Parser 采样解析，获取准确的平均每行大小
        parser = get_parser(self.format_type)
        sample_rows = 100
        sampled_bytes = 0
        sampled_count = 0

        for p in self.paths:
            with open(p, "rb") as f:
                for row in parser.parse_to_dataframe(f, chunk_size=sample_rows):
                    sampled_bytes += len(json.dumps(row).encode("utf-8"))
                    sampled_count += 1
                    if sampled_count >= sample_rows:
                        break
            if sampled_count >= sample_rows:
                break

        if sampled_count == 0:
            # 无法采样，用经验值
            return max(1, int(total_size / 200))

        avg_row_bytes = sampled_bytes / sampled_count
        return max(1, int(total_size / avg_row_bytes))'''

c = c.replace(old, new)
ds.write_text(c)
print("✅ FileDataSource.estimate_total_rows 已修复")
print("现在使用 DataParser 采样，正确处理 CSV/Parquet 等格式")
