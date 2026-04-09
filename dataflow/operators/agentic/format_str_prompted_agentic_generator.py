import pandas as pd
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow import get_logger
from typing import Union, Any, Set

from dataflow.utils.storage import DataFlowStorage
from dataflow.utils.storage.data_parser import clean_surrogates
from dataflow.core import OperatorABC
from dataflow.serving.cli_openclaw_serving import CLIOpenClawServing
from dataflow.core.prompt import prompt_restrict, PromptABC, DIYPromptABC

from dataflow.prompts.core_text import FormatStrPrompt


@prompt_restrict(
    FormatStrPrompt,
)
@OPERATOR_REGISTRY.register()
class FormatStrPromptedAgenticGenerator(OperatorABC):
    """
    基于模板化提示词的 Agent 生成算子。

    与 FormatStrPromptedGenerator 的区别：
    - 只能使用 CLIOpenClawServing
    - 支持 input_files_data_key 参数，用于传递文件内容数据
    """

    def __init__(
        self,
        llm_serving: CLIOpenClawServing,
        system_prompt: str = "You are a helpful agent.",
        prompt_template: Union[FormatStrPrompt, DIYPromptABC] = FormatStrPrompt,
        json_schema: dict = None,
    ):
        self.logger = get_logger()
        self.llm_serving = llm_serving
        self.system_prompt = system_prompt
        self.prompt_template = prompt_template
        self.json_schema = json_schema
        if prompt_template is None:
            raise ValueError("prompt_template cannot be None")

    def run(
        self,
        storage: DataFlowStorage,
        input_files_data_key: str,
        output_key: str = "generated_content",
        **input_keys: Any,
    ):
        """
        运行生成器。

        Args:
            storage: DataFlowStorage 实例
            output_key: 输出生成内容字段名
            input_files_data_key: 可选，FileContextGenerator 输出的 key，
                                 用于传递文件内容数据给 CLIOpenClawServing
            **input_keys: 输入字段映射字典
        """
        self.storage: DataFlowStorage = storage
        self.output_key = output_key
        self.logger.info("Running FormatStrPromptedAgenticGenerator...")
        self.input_keys = input_keys
        self.input_files_data_key = input_files_data_key

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

        # Generate content using the CLIOpenClawServing
        generated_outputs = self.llm_serving.generate_from_input(
            user_inputs=llm_inputs,
            system_prompt=self.system_prompt,
            json_schema=self.json_schema,
            input_files_data=input_files_data,
        )

        dataframe[self.output_key] = clean_surrogates(generated_outputs)
        dataframe[f".prompt.system.{self.output_key}"] = self.system_prompt
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
                "- 支持 input_files_data_key 参数，用于传递 FileContextGenerator 合成的文件数据\n\n"
                "输入参数：\n"
                "- llm_serving：CLIOpenClawServing 服务对象\n"
                "- prompt_template：提示词模板对象（StrFormatPrompt 或 DIYPromptABC）\n"
                "- input_keys：输入字段映射字典\n"
                "- output_key：输出生成内容字段名\n"
                "- input_files_data_key：FileContextGenerator 的输出 key，用于读取合成的文件数据\n\n"
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
