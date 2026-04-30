import json

from typing import Any, Optional

import pandas as pd

from dataflow import get_logger
from dataflow.core import OperatorABC
from dataflow.serving.agent.cli_openclaw_serving import CLIOpenClawServing
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage
from dataflow.utils.storage.data_parser import clean_surrogates


@OPERATOR_REGISTRY.register()
class FormatStrPromptedAgenticGenerator(OperatorABC):
    """
    基于模板化提示词的 Agent 对话生成算子（用户模拟器版本）。

    核心设计：
    - 不再区分"验证器"和"用户"，Verifier 就是用户模拟器（User Simulator）
    - 通过 user_prompt_template 指导 Verifier 生成自然对话
    - 支持渐进式多轮对话（30-50 轮），信息逐步暴露
    - Verifier 负责生成第一条消息，而不是从输入字段提取

    与旧版本的区别：
    - 移除 verification_prompt_template → 改为 user_prompt_template
    - 移除 enable_verification → 始终启用对话模式
    - max_verification_rounds → max_rounds（更直观的命名）
    - 不再需要"验证"概念，只有"对话"概念
    """

    def __init__(
        self,
        llm_serving: CLIOpenClawServing,
        user_prompt_template: str,
        max_rounds: int = 50,
    ):
        """
        初始化对话生成器。

        Args:
            llm_serving: CLIOpenClawServing 服务对象
            user_prompt_template: 用户模拟器提示词模板
                                 Verifier 将在此模板指导下模拟真实用户，与 Agent 进行多轮对话
                                 模板需要指导 Verifier：
                                 1. 如何生成第一条消息（动态生成，不是从输入提取）
                                 2. 如何渐进式暴露信息（每轮 30%-60%）
                                 3. 如何参考 milestones/retrieval_points/dialogue_scripts
                                 4. 如何自然引导 Agent 完成任务
            max_rounds: 最大对话轮数，默认 50（支持 30-50 轮自然对话）
        """
        self.logger = get_logger()
        self.llm_serving = llm_serving
        self.user_prompt_template = user_prompt_template
        self.max_rounds = max_rounds

    def run_dataframe(
        self,
        dataframe: pd.DataFrame,
        input_files_data_key: str,
        input_skills_key: Optional[str] = None,
        output_key: str = "generated_content",
        **input_keys: Any,
    ) -> pd.DataFrame:
        self.logger.info(f"Loading, number of rows: {len(dataframe)}")

        input_files_data = []
        for _, row in dataframe.iterrows():
            input_files_data.append(row[input_files_data_key])

        input_skills_data = []
        for _, row in dataframe.iterrows():
            if input_skills_key:
                input_skills_data.append(row[input_skills_key])
            else:
                input_skills_data.append([])

        # 构建 user prompt templates（简单替换）
        user_prompt_templates = self._build_user_prompt_templates(
            dataframe, **input_keys
        )

        # Generate content using the CLIOpenClawServing
        generated_outputs = self.llm_serving.generate_from_input(
            user_inputs=user_prompt_templates,
            input_files_data=input_files_data,
            input_skills_data=input_skills_data,
            max_verification_rounds=self.max_rounds,  # 改为 max_rounds
        )

        dataframe[output_key] = clean_surrogates(generated_outputs)

        return dataframe

    def run(
        self,
        storage: DataFlowStorage,
        input_files_data_key: str,
        input_skills_key: Optional[str] = None,
        output_key: str = "generated_content",
        **input_keys: Any,
    ):
        """
        运行对话生成器。

        Args:
            storage: DataFlowStorage 实例
            input_files_data_key: FileContextGenerator 输出的 key，用于传递文件内容数据
            input_skills_key: 存储 skill 路径列表的 DataFrame 列名（可选）
            output_key: 输出生成内容字段名
            **input_keys: 输入字段映射字典，用于构建 user prompt（milestones, retrieval_points, dialogue_scripts 等）
        """
        self.logger.info(
            "Running FormatStrPromptedAgenticGenerator (User Simulator Mode)..."
        )

        dataframe = storage.read("dataframe")
        self.run_dataframe(
            dataframe,
            input_files_data_key,
            input_skills_key,
            output_key,
            **input_keys,
        )

        storage.write(dataframe)
        return output_key

    def _build_user_prompt_templates(self, dataframe: pd.DataFrame, **input_keys: Any):
        """
        为每行构建 user prompt（用户模拟器提示词），替换所有 {input_xxxx_key} 占位符。

        逻辑：
        1. 遍历 input_keys 字典
        2. 对于每个 {input_xxx_key} 占位符，从 dataframe 的该行读取字段值
        3. 转换为 JSON 字符串并替换
        4. 返回每行不同的 template 列表

        Returns:
            List[str]: 每行对应的 user prompt 模板列表
        """
        user_prompt_templates = []
        for idx, row in enumerate(dataframe.to_dict(orient="records")):
            template = self.user_prompt_template

            # 简单替换：遍历 input_keys 字典
            for placeholder_key, field_name in input_keys.items():
                if field_name and field_name in row:
                    value = row[field_name]
                    value_str = json.dumps(value, ensure_ascii=False, indent=2)
                    template = template.replace(f"{{{placeholder_key}}}", value_str)
                else:
                    self.logger.warning(
                        f"行 {idx}: 字段 '{field_name}' 不存在，跳过占位符 '{{{placeholder_key}}}'"
                    )

            user_prompt_templates.append(template)

        return user_prompt_templates

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "基于模板化提示词（Prompt Template）的 Agent 对话生成算子（用户模拟器版本）。"
                "该算子使用用户模拟器提示模板，结合输入数据中的字段自动构造完整提示词，"
                "调用 CLIOpenClawServing 模拟真实用户与 Agent 进行多轮对话。\n\n"
                "核心设计：\n"
                "- Verifier = User Simulator（用户模拟器），不再是'验证器'\n"
                "- 支持渐进式多轮对话（30-50 轮），信息逐步暴露（每轮 30%-60%）\n"
                "- Verifier 负责动态生成第一条消息，不是从输入字段提取\n"
                "- 参考 milestones/retrieval_points/dialogue_scripts 指导对话\n\n"
                "与旧版本的区别：\n"
                "- verification_prompt_template → user_prompt_template\n"
                "- enable_verification → 始终启用对话模式\n"
                "- max_verification_rounds → max_rounds\n"
                "- 移除'验证'概念，只有'对话'概念\n\n"
                "与 FormatStrPromptedGenerator 的区别：\n"
                "- 只能使用 CLIOpenClawServing\n"
                "- 支持 input_files_data_key 参数，用于传递 FileContextGenerator 合成的文件数据\n"
                "- 支持 input_skills_key 参数，用于传递技能名称列表（路径拼接由 CLIOpenClawServing 负责）\n"
                "- 支持 user_prompt_template 参数，用于指导用户模拟器对话\n"
                "- **新增**：user_prompt_template 支持 {input_xxxx_key} 占位符，自动替换为对应字段值\n\n"
                "输入参数：\n"
                "- llm_serving：CLIOpenClawServing 服务对象\n"
                "- user_prompt_template：用户模拟器提示词模板（StrFormatPrompt 或 DIYPromptABC）\n"
                "- input_keys：输入字段映射字典\n"
                "- output_key：输出生成内容字段名\n"
                "- input_files_data_key：FileContextGenerator 的输出 key，用于读取合成的文件数据\n"
                "- input_skills_key：DataFrame 列名，包含技能名称列表\n"
                "- max_rounds：最大对话轮数\n\n"
                "输出参数：\n"
                "- 包含生成结果的新 DataFrame\n"
                "- 返回输出字段名，以便后续算子引用\n\n"
                "使用场景：\n"
                "适用于需要通过用户模拟器进行渐进式多轮对话（30-50 轮），"
                "结合 FileContextGenerator 合成文件数据的场景。"
            )
        elif lang == "en":
            return (
                "An Agent-based operator for dialogue generation using templated prompts (User Simulator Mode). "
                "This operator uses a user simulator prompt template to automatically construct full prompts "
                "from input data fields and simulate natural multi-turn dialogues with Agent via CLIOpenClawServing.\n\n"
                "Core Design:\n"
                "- Verifier = User Simulator (no more 'verification' concept)\n"
                "- Supports progressive multi-turn dialogue (30-50 rounds) with gradual information exposure (30%-60% per turn)\n"
                "- User Simulator dynamically generates the first message (not extracted from input fields)\n"
                "- References milestones/retrieval_points/dialogue_scripts to guide dialogue\n\n"
                "Differences from Old Version:\n"
                "- verification_prompt_template → user_prompt_template\n"
                "- enable_verification → always enabled (dialogue mode)\n"
                "- max_verification_rounds → max_rounds\n"
                "- Removed 'verification' concept, only 'dialogue' concept\n\n"
                "Differences from FormatStrPromptedGenerator:\n"
                "- Only supports CLIOpenClawServing\n"
                "- Supports input_files_data_key parameter to pass synthetic file data from FileContextGenerator\n"
                "- Supports input_skills_key parameter to pass skill name lists (path concatenation handled by CLIOpenClawServing)\n"
                "- Supports user_prompt_template parameter for user simulator dialogue guidance\n"
                "- **New**: user_prompt_template supports {input_xxxx_key} placeholders, automatically replaced with corresponding field values\n\n"
                "Input Parameters:\n"
                "- llm_serving: CLIOpenClawServing object\n"
                "- user_prompt_template: User simulator prompt template (StrFormatPrompt or DIYPromptABC)\n"
                "- input_keys: Dictionary mapping DataFrame column names to template fields\n"
                "- output_key: Field name for generated content\n"
                "- input_files_data_key: Output key from FileContextGenerator for reading synthetic file data\n"
                "- input_skills_key: DataFrame column name containing skill name lists\n"
                "- max_rounds: Maximum dialogue rounds\n\n"
                "Output Parameters:\n"
                "- DataFrame containing generated outputs\n"
                "- Returns the output field name for downstream operator reference\n\n"
                "Use Case:\n"
                "Ideal for tasks requiring progressive multi-turn dialogue (30-50 rounds) with synthetic file data from FileContextGenerator."
            )
        else:
            return "FormatStrPromptedAgenticGenerator generates user-Agent dialogues based on templated prompts using CLIOpenClawServing (User Simulator Mode)."
