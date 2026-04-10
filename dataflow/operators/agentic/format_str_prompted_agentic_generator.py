import os

from typing import Union, Any, Optional

from dataflow import get_logger
from dataflow.core import OperatorABC
from dataflow.core.prompt import prompt_restrict, DIYPromptABC
from dataflow.prompts.core_text import FormatStrPrompt
from dataflow.serving.cli_openclaw_serving import CLIOpenClawServing
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage
from dataflow.utils.storage.data_parser import clean_surrogates


@prompt_restrict(FormatStrPrompt)
@OPERATOR_REGISTRY.register()
class FormatStrPromptedAgenticGenerator(OperatorABC):
    """
    基于模板化提示词的 Agent 生成算子。

    与 FormatStrPromptedGenerator 的区别：
    - 只能使用 CLIOpenClawServing
    - 支持 input_files_data_key 参数，用于传递文件内容数据
    - 支持 input_skills_key 和 input_skills_dir 参数，用于动态加载 skills
    - 支持 verification_prompt_template 参数，用于任务验证循环
    """

    def __init__(
        self,
        llm_serving: CLIOpenClawServing,
        prompt_template: Union[FormatStrPrompt, DIYPromptABC],
        input_skills_dir: Optional[str] = None,
        verification_prompt_template: Optional[str] = None,
        enable_verification: bool = False,
        max_verification_rounds: int = 5,
    ):
        """
        初始化生成器。

        Args:
            llm_serving: CLIOpenClawServing 服务对象
            prompt_template: 提示词模板对象
            input_skills_dir: skill 路径的前缀目录（可选）
            verification_prompt_template: 验证提示词模板（可选），用于任务验证循环
            enable_verification: 是否启用自动验证循环，默认 False
            max_verification_rounds: 最大验证轮数，默认 5
        """
        self.logger = get_logger()
        self.llm_serving = llm_serving
        self.prompt_template = prompt_template
        self.input_skills_dir = input_skills_dir
        self.verification_prompt_template = verification_prompt_template
        self.enable_verification = enable_verification
        self.max_verification_rounds = max_verification_rounds
        if prompt_template is None:
            raise ValueError("prompt_template cannot be None")

    def run(
        self,
        storage: DataFlowStorage,
        input_files_data_key: str,
        input_skills_key: Optional[str] = None,
        output_key: str = "generated_content",
        **input_keys: Any,
    ):
        """
        运行生成器。

        Args:
            storage: DataFlowStorage 实例
            input_files_data_key: FileContextGenerator 输出的 key，用于传递文件内容数据
            input_skills_key: 存储 skill 路径列表的 DataFrame 列名（可选）
            output_key: 输出生成内容字段名
            **input_keys: 输入字段映射字典
        """
        self.storage: DataFlowStorage = storage
        self.output_key = output_key
        self.logger.info("Running FormatStrPromptedAgenticGenerator...")
        self.input_keys = input_keys
        self.input_files_data_key = input_files_data_key
        self.input_skills_key = input_skills_key
        self.logger.info(f"input_skills_dir: {self.input_skills_dir}")

        need_fields = set(input_keys.keys())
        # Load the raw dataframe from the input file
        dataframe = storage.read("dataframe")
        self.logger.info(f"Loading, number of rows: {len(dataframe)}")
        llm_inputs = []

        for idx, row in dataframe.iterrows():
            key_dict = {}
            for key in need_fields:
                key_dict[key] = row[input_keys[key]]
            prompt_text = self.prompt_template.build_prompt(need_fields, **key_dict)
            llm_inputs.append(prompt_text)

        self.logger.info(f"Prepared {len(llm_inputs)} prompts for LLM generation.")

        # 如果有 input_files_data_key，从 storage 读取文件数据
        input_files_data = []
        # 读取 FileContextGenerator 输出的文件内容数据
        # 格式：[{output_key: {filename: content_data}}, ...]
        for idx, row in dataframe.iterrows():
            if input_files_data_key in row and isinstance(
                row[input_files_data_key], dict
            ):
                input_files_data.append(row[input_files_data_key])
            else:
                input_files_data.append({})

        # 准备 input_skills_data：拼接 skill 路径
        input_skills_data = []
        for idx, row in dataframe.iterrows():
            if (
                input_skills_key
                and input_skills_key in row
                and isinstance(row[input_skills_key], list)
            ):
                # 使用实例变量 input_skills_dir 拼接路径
                skills = row[input_skills_key]
                if self.input_skills_dir:
                    skills = [os.path.join(self.input_skills_dir, s) for s in skills]
                input_skills_data.append(skills)
                self.logger.debug(f"Row {idx}: 准备 {len(skills)} 个 skills")
            else:
                input_skills_data.append([])

        self.logger.info(
            f"Prepared {len(input_skills_data)} skill lists for generation."
        )

        # Generate content using the CLIOpenClawServing
        generated_outputs = self.llm_serving.generate_from_input(
            user_inputs=llm_inputs,
            input_files_data=input_files_data,
            input_skills_data=input_skills_data,
            enable_verification=self.enable_verification,
            verification_prompt_template=self.verification_prompt_template,
            max_verification_rounds=self.max_verification_rounds,
        )

        dataframe[self.output_key] = clean_surrogates(generated_outputs)
        dataframe[f".prompt.user.{self.output_key}"] = llm_inputs

        self.storage.write(dataframe)

        return output_key

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "基于模板化提示词（Prompt Template）的 Agent 生成算子。"
                "该算子使用用户定义的提示模板（StrFormatPrompt 或 DIYPrompt），"
                "结合输入数据中的字段自动构造完整提示词并调用 CLIOpenClawServing 生成结果。\n\n"
                "与 FormatStrPromptedGenerator 的区别：\n"
                "- 只能使用 CLIOpenClawServing\n"
                "- 支持 input_files_data_key 参数，用于传递 FileContextGenerator 合成的文件数据\n"
                "- 支持 input_skills_key 和 input_skills_dir 参数，用于动态加载 skills\n"
                "- 支持 verification_prompt_template 参数，用于任务验证循环\n\n"
                "输入参数：\n"
                "- llm_serving：CLIOpenClawServing 服务对象\n"
                "- prompt_template：提示词模板对象（StrFormatPrompt 或 DIYPromptABC）\n"
                "- input_keys：输入字段映射字典\n"
                "- output_key：输出生成内容字段名\n"
                "- input_files_data_key：FileContextGenerator 的输出 key，用于读取合成的文件数据\n"
                "- input_skills_key：DataFrame 列名，包含 skill 路径列表\n"
                "- input_skills_dir：skill 路径的前缀目录\n"
                "- verification_prompt_template：验证提示词模板（可选）\n"
                "- enable_verification：是否启用自动验证循环\n"
                "- max_verification_rounds：最大验证轮数\n\n"
                "输出参数：\n"
                "- 包含生成结果的新 DataFrame\n"
                "- 返回输出字段名，以便后续算子引用\n\n"
                "使用场景：\n"
                "适用于需要通过模板化提示构建多样输入、批量生成文本内容，"
                "且需要结合 FileContextGenerator 合成文件数据的场景。"
            )
        elif lang == "en":
            return (
                "An Agent-based operator for content generation using templated prompts. "
                "This operator uses a user-defined prompt template (StrFormatPrompt or DIYPromptABC) "
                "to automatically construct full prompts from input data fields and generate outputs via CLIOpenClawServing.\n\n"
                "Differences from FormatStrPromptedGenerator:\n"
                "- Only supports CLIOpenClawServing\n"
                "- Supports input_files_data_key parameter to pass synthetic file data from FileContextGenerator\n\n"
                "Input Parameters:\n"
                "- llm_serving: CLIOpenClawServing object\n"
                "- prompt_template: Prompt template object (StrFormatPrompt or DIYPromptABC)\n"
                "- input_keys: Dictionary mapping DataFrame column names to template fields\n"
                "- output_key: Field name for generated content\n"
                "- input_files_data_key: Output key from FileContextGenerator for reading synthetic file data\n\n"
                "Output Parameters:\n"
                "- DataFrame containing generated outputs\n"
                "- Returns the output field name for downstream operator reference\n\n"
                "Use Case:\n"
                "Ideal for tasks requiring templated prompt-driven generation with synthetic file data from FileContextGenerator."
            )
        else:
            return "FormatStrPromptedAgenticGenerator generates text based on templated prompts using CLIOpenClawServing."
