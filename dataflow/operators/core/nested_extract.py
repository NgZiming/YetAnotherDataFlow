"""
嵌套 JSON 提取算子模块

提供从嵌套 JSON 结构中提取指定字段到扁平化列的功能。
支持点号路径（如 "user.address.city"）和数组索引（如 "items[0].name"）语法。
"""

import json
from typing import Any

import pandas as pd

from dataflow import get_logger
from dataflow.utils.storage import DataFlowStorage
from dataflow.core import OperatorABC
from dataflow.utils.registry import OPERATOR_REGISTRY


def get_nested_value(d: dict, path: str) -> Any:
    """
    从嵌套字典中根据路径提取值。

    支持以下路径语法：
    - 点号分隔： "user.address.city"
    - 数组索引： "items[0].name"
    - 混合使用： "users[0].address.tags[1]"

    如果路径中的某个节点是 JSON 字符串，会自动尝试解析。
    如果路径不存在或解析失败，返回 None。

    Args:
        d: 源字典（可能包含嵌套结构和 JSON 字符串）
        path: 提取路径，使用点号和方括号语法

    Returns:
        提取到的值，如果路径不存在或解析失败则返回 None

    Examples:
        >>> data = {"user": {"name": "Alice", "tags": ["admin", "user"]}}
        >>> get_nested_value(data, "user.name")
        'Alice'
        >>> get_nested_value(data, "user.tags[0]")
        'admin'
        >>> get_nested_value(data, "user.age")
        None
    """
    parts = path.split(".")

    current = d
    for part_idx, part in enumerate(parts):
        # 处理数组索引语法，如 "items[0]"
        number = None
        if "[" in part and part.endswith("]"):
            idx = part.find("[")
            try:
                number = int(part[idx + 1 : -1])
            except ValueError:
                return None
            part = part[:idx]

        # 检查键是否存在
        if part not in current:
            return None

        # 如果当前值是字符串且不是最后一段，尝试解析为 JSON
        if isinstance(current[part], str) and part_idx != len(parts) - 1:
            try:
                current[part] = json.loads(current[part])
            except (json.JSONDecodeError, TypeError):
                return None

        # 进入下一层
        current = current[part]

        # 处理数组索引
        if number is not None:
            if not isinstance(current, list) or number < 0 or number >= len(current):
                return None
            current = current[number]

    return current


@OPERATOR_REGISTRY.register()
class NestExtractOperator(OperatorABC):
    """
    嵌套 JSON 提取算子

    从 DataFrame 的 JSON 列中提取嵌套字段到新的输出列。
    支持复杂的路径语法，包括点号分隔和数组索引。

    配置参数：
        - input_<name>_key: 输入列名（包含 JSON 数据）
        - output_<name>_key: 对应的输出列名（提取后的扁平列）

    示例配置：
        NestExtractOperator(
            input_user_key="user_json",      # 输入列：user_json
            output_user_name_key="user_name", # 输出列：user_name，从 user_json.name 提取
            output_user_age_key="user_age",   # 输出列：user_age，从 user_json.age 提取
        )

    Attributes:
        keys_map: 映射字典，键为输入列名，值为输出列名
        logger: 日志记录器
    """

    def __init__(self, remove_all_keys: bool = False):
        """
        初始化嵌套提取算子。
        """
        self.logger = get_logger()
        self.logger.info("Initializing NestExtractOperator")
        self.remove_all_keys = remove_all_keys

    @staticmethod
    def get_desc(lang: str = "zh") -> str:
        """
        获取算子描述。

        描述该算子的核心功能：从 DataFrame 列中的嵌套 JSON 结构（支持点号路径和数组索引）
        提取指定字段，并扁平化为新的输出列。

        Args:
            lang: 语言代码，"zh" 为中文，其他为英文

        Returns:
            算子描述字符串（中文或英文）

        Example:
            >>> NestExtractOperator.get_desc("zh")
            '根据点号/数组路径从 JSON 列提取字段到扁平输出列'
            >>> NestExtractOperator.get_desc("en")
            'Extract fields from JSON columns to flat output columns using dot/array paths'
        """
        return (
            "根据点号/数组路径从 JSON 列提取字段到扁平输出列"
            if lang == "zh"
            else "Extract fields from JSON columns to flat output columns using dot/array paths"
        )

    def run(self, storage: DataFlowStorage, **kwargs):
        """
        执行嵌套 JSON 提取操作。

        从存储中读取 DataFrame，根据配置的映射关系提取嵌套字段。
        如果任何提取的字段值为 None，则丢弃该行。
        将结果写回存储。

        Args:
            storage: DataFlowStorage 对象，用于读取输入数据和写入结果
            remove_all_keys: 是否移除原始列，只保留提取后的列
                - True: 新行只包含提取的字段
                - False: 新行包含原始字段 + 提取的字段

        Raises:
            KeyError: 如果存储中不存在 "dataframe" 键
            ValueError: 如果读取的数据不是 DataFrame 格式

        Example:
            >>> storage.write(pd.DataFrame({"json_col": ['{"name": "Alice"}']}))
            >>> op = NestExtractOperator(input_data_key="json_col", output_name_key="name")
            >>> op.run(storage)
            >>> result = storage.read("dataframe")
            >>> print(result.columns)  # Index(['json_col', 'name'], dtype='object')
        """
        self.logger.info("Starting NestExtractOperator execution")

        # 解析输入输出列映射（从 kwargs 中）
        keys_map: dict[str, str] = {}

        # 解析输入列配置
        input_keys = {}
        for key, value in kwargs.items():
            if key.startswith("input_") and key.endswith("_key"):
                name = key[6:-4]  # 提取中间的 name 部分
                input_keys[name] = value
                self.logger.debug(f"Registered input key: {name} -> {value}")

        # 解析输出列配置
        output_keys = {}
        for key, value in kwargs.items():
            if key.startswith("output_") and key.endswith("_key"):
                name = key[7:-4]  # 提取中间的 name 部分
                output_keys[name] = value
                self.logger.debug(f"Registered output key: {name} -> {value}")

        # 构建输入到输出的映射
        for name, input_key in input_keys.items():
            if name in output_keys:
                output_key = output_keys[name]
                keys_map[input_key] = output_key
                self.logger.info(f"Created mapping: {input_key} -> {output_key}")
            else:
                self.logger.warning(f"No output key found for input '{name}', skipping")

        self.logger.info(
            f"NestExtractOperator initialized with {len(keys_map)} mappings"
        )

        # 读取输入数据
        self.logger.debug("Reading DataFrame from storage")
        df: pd.DataFrame = storage.read("dataframe")
        self.logger.info(
            f"Read DataFrame with {len(df)} rows and {len(df.columns)} columns"
        )

        # 转换为字典列表以便处理
        rows = df.to_dict("records")
        self.logger.debug(f"Converted to {len(rows)} dictionary records")

        # 处理每一行
        rtn_rows: list[dict] = []
        processed_count = 0
        skipped_count = 0
        error_count = 0

        for row_idx, row in enumerate(rows):
            try:
                # 初始化新行
                if self.remove_all_keys:
                    new_row = {}
                    self.logger.debug(
                        f"Row {row_idx}: remove_all_keys=True, starting with empty row"
                    )
                else:
                    new_row = row.copy()
                    self.logger.debug(
                        f"Row {row_idx}: remove_all_keys=False, copying original row"
                    )

                # 执行提取操作
                has_none = False
                for input_key, output_key in keys_map.items():
                    extracted_value = get_nested_value(row, input_key)

                    # 如果提取值为 None，标记该行需要丢弃
                    if extracted_value is None:
                        has_none = True
                        self.logger.debug(
                            f"Row {row_idx}: Extracted None for {input_key} -> {output_key}, marking for skip"
                        )
                        break  # 提前退出，不需要继续提取

                    new_row[output_key] = extracted_value

                # 如果有任何提取值为 None，丢弃该行
                if has_none:
                    skipped_count += 1
                    continue

                rtn_rows.append(new_row)
                processed_count += 1

            except Exception as e:
                self.logger.error(f"Row {row_idx}: Processing failed - {e}")
                error_count += 1
                # 继续处理其他行，不中断整个流程

        # 创建结果 DataFrame
        result_df = pd.DataFrame(rtn_rows)

        # 写回存储
        self.logger.info(f"Writing result DataFrame with {len(result_df)} rows")
        self.logger.debug(f"Result columns: {list(result_df.columns)}")
        storage.write(result_df)

        # 输出统计信息
        self.logger.info(
            f"NestExtractOperator completed: "
            f"total={len(rows)}, "
            f"processed={processed_count}, "
            f"skipped_none={skipped_count}, "
            f"errors={error_count}, "
            f"output_columns={len(result_df.columns)}"
        )
