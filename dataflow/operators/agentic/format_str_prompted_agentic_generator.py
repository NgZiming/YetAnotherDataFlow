import pandas as pd

from dataflow import get_logger
from dataflow.core import OperatorABC
from dataflow.core.agentic.serving import TaskDefinition
from dataflow.serving.agent.cli_openclaw_serving import CLIOpenClawServing
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage
from dataflow.utils.storage.data_parser import clean_surrogates


@OPERATOR_REGISTRY.register()
class FormatStrPromptedAgenticGenerator(OperatorABC):
    """
    基于结构化任务定义的 Agent 对话生成算子（用户模拟器版本）。

    核心设计：
    - 适配 complex-task 格式，将 milestones 与 retrieval_points 按 stage 合并
    - 合并后的每个 milestone 包含：goal, success_criteria, required_clues, expected_retrieval_point
    - 最终输出为标准的 TrajectoryDict 轨迹
    """

    def __init__(
        self,
        llm_serving: CLIOpenClawServing,
        max_rounds: int = 50,
    ):
        self.logger = get_logger()
        self.llm_serving = llm_serving
        self.max_rounds = max_rounds

    def _merge_milestones_and_retrieval_points(self, milestones, retrieval_points):
        """
        将 milestones 和 retrieval_points 按 stage 合并。

        Args:
            milestones: List[Dict], 每个元素包含 stage, goal, success_criteria 等
            retrieval_points: List[Dict], 每个元素包含 stage, expected_retrieval_point

        Returns:
            List[Dict]: 合并后的里程碑列表，每个元素包含原有字段 + expected_retrieval_point
        """
        # 构建 stage -> retrieval_point 的映射
        retrieval_map = {
            rp.get("stage"): rp.get("expected_retrieval_point")
            for rp in retrieval_points
        }

        merged_milestones = []
        for milestone in milestones:
            stage = milestone.get("stage")
            merged_item = milestone.copy()

            # 如果存在对应的 retrieval_point，合并进去
            if stage in retrieval_map:
                merged_item["expected_retrieval_point"] = retrieval_map[stage]

            merged_milestones.append(merged_item)

        return merged_milestones

    def run_dataframe(
        self,
        dataframe: pd.DataFrame,
        input_files_data_key: str,
        input_skills_key: str,
        input_question_key: str,
        input_milestones_key: str,
        input_retrieval_points_key: str,
        input_user_dialogue_scripts_key: str,
        output_key: str = "generated_content",
    ) -> pd.DataFrame:
        self.logger.info(f"Loading, number of rows: {len(dataframe)}")

        tasks: list[TaskDefinition] = []
        for idx, row in dataframe.iterrows():
            # 提取原始数据
            milestones = row[input_milestones_key]
            retrieval_points = row[input_retrieval_points_key]

            # 合并 milestones 和 retrieval_points
            merged_milestones = self._merge_milestones_and_retrieval_points(
                milestones, retrieval_points
            )

            # 组装 TaskDefinition
            task_def: TaskDefinition = {
                "task_id": f"task_{idx}",
                # 核心引导数据（合并后的 milestones）
                "question": row[input_question_key],
                "milestones": merged_milestones,
                "user_dialogue_scripts": row[input_user_dialogue_scripts_key],
                # 资源定义
                "files_contents": row[input_files_data_key],
                "skills": row[input_skills_key],
                "max_rounds": self.max_rounds,
                "global_context": {},
            }
            tasks.append(task_def)

        # 调用服务层接口
        generated_outputs = self.llm_serving.generate_from_input(tasks)

        # 将轨迹字典序列化存储
        dataframe[output_key] = [clean_surrogates(res) for res in generated_outputs]

        return dataframe

    def run(
        self,
        storage: DataFlowStorage,
        input_files_data_key: str,
        input_skills_key: str,
        input_question_key: str,
        input_milestones_key: str,
        input_retrieval_points_key: str,
        input_user_dialogue_scripts_key: str,
        output_key: str = "generated_content",
    ):
        """
        运行对话生成器。
        """
        self.logger.info(
            "Running FormatStrPromptedAgenticGenerator (Complex Task Mode)..."
        )

        dataframe = storage.read("dataframe")
        self.run_dataframe(
            dataframe,
            input_files_data_key,
            input_skills_key,
            input_question_key,
            input_milestones_key,
            input_retrieval_points_key,
            input_user_dialogue_scripts_key,
            output_key,
        )

        storage.write(dataframe)
        return output_key

    @staticmethod
    def get_desc(lang: str = "zh"):
        if lang == "zh":
            return (
                "基于结构化任务定义的 Agent 对话生成算子（用户模拟器版本）。\n\n"
                "核心设计：\n"
                "- 自动将 milestones 与 retrieval_points 按 stage 合并，形成完整的任务引导信息\n"
                "- 支持动态 Persona：模拟用户人格随对话阶段在 user_dialogue_scripts 中自动切换\n"
                "- 输出标准的 TrajectoryDict 对话轨迹\n\n"
                "输入参数 (均为必需)：\n"
                "- input_files_data_key：文件内容数据的列名\n"
                "- input_skills_key：技能列表的列名\n"
                "- input_question_key：任务问题的列名\n"
                "- input_milestones_key：里程碑数据的列名\n"
                "- input_retrieval_points_key：推理锚点数据的列名\n"
                "- input_user_dialogue_scripts_key：对话脚本数据的列名\n"
                "- output_key：输出轨迹的列名\n"
            )
        return "Agent-based operator for dialogue generation using User Simulator architecture."
