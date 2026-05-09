import asyncio
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from dataflow.logger import get_logger
from dataflow.core.agentic import (
    LLMClientABC,
    StepSchema,
    UserStage,
    UserStep,
)
from dataflow.serving.agent.user_v2.models import (
    FileContext,
    FileEvidence,
    AgentContext,
    AgentBehavior,
    DialogueContext,
)

logger = get_logger()


class PerceptionStageV2(UserStage):
    """
    Perception Stage V2: Evidence-Driven Context Extraction.
    Transforms raw agent trajectories and files into a structured cognitive snapshot.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.llm_config = llm_config or {}

        self.steps = [
            UserStep(
                name="FileSensor",
                schema=StepSchema(
                    input_keys=["file_contents", "question"],
                    output_key="file_context",
                    output_type=FileContext,
                    prompt_template="""你是一个极度严谨的证据提取专家。你的任务是从文件中提取与用户目标直接相关的【实证证据】。

## 输入
- 文件路径(file_path)：{file_path}
- 文件内容：
```
{file_content}
```
- 用户初始问题(question)：{question}

## 输入字段说明
- **file_path**: 文件的完整路径，用于识别文件类型、模块位置及在证据引用中作为唯一标识。
- **file_content**: 文件的原始内容。你必须在此文本中寻找直接证据，严禁基于常识或猜测生成内容。
- **question**: 用户的初始目标描述。它是你判断信息“相关性”的唯一锚点。只有能直接证明或反驳该目标实现的片段才被视为相关。

## 认知逻辑
1. **目标锚定**：分析用户问题，确定该文件在任务中扮演的角色（如：定义了核心类、提供了配置参数、记录了分析结论）。
2. **实证提取**：在文件中寻找能直接证明或反驳任务目标的具体片段。
3. **证据构建**：为每一个发现构建一个包含 path, fact, evidence_snippet, relevance 的 JSON 对象。

## 提取标准 (Evidence Standard)
- **具体化**：禁止使用“文件讨论了 X”这种模糊描述。必须写成“文件中明确定义了 X 函数，其逻辑是 Y”。
- **片段化**：`evidence_snippet` 必须是原封不动的文本截取，严禁改写。
- **相关性**：只提取与 {question} 强相关的信息。无关内容直接忽略。

## 输出格式
必须输出一个单一的 JSON 对象（不要包装在列表或其他对象中），包含以下字段：
- `path`: 文件的完整路径。
- `fact`: 提取的实证事实。
- `evidence_snippet`: 原封不动的文本截取。
- `relevance`: 该证据与 {question} 的相关性分析。

## 示例
问题："如何实现用户认证？"
输出：
{{
  "path": "auth.py",
  "fact": "使用 JWT 令牌进行身份验证，核心逻辑在 verify_token 函数中",
  "evidence_snippet": "def verify_token(token):\\n    payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])",
  "relevance": "直接定义了认证的实现机制"
}}
""",
                    # Note: In v2, we'll handle the aggregation of multiple files in the execute method
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="AgentSensor",
                schema=StepSchema(
                    input_keys=["file_context", "agent_outputs"],
                    output_key="agent_context",
                    output_type=AgentContext,
                    prompt_template="""你是一个行为分析专家。通过分析 Agent 的输出轨迹，还原其真实的认知路径和行为模式。

## 输入
- Agent 输出轨迹(agent_outputs)：{agent_outputs}
- 物理文件实证(file_context)：{file_context}

## 输入字段说明
- **agent_outputs**: Agent 的回答历史列表。
  - 格式：`["回答 1", "回答 2", ...]`
  - 顺序：按时间顺序排列，索引 0 为最早的一次对话。
  - 内容：包含 Agent 的推理、工具调用结果及最终回复。你需要从中识别其“动作”与“结论”。
- **file_context**: 此前提取的物理文件证据。
  - 用途：用于验证 Agent 的“发现”是否真实存在于代码中。如果 Agent 声称发现了 X，但 `file_context` 中无相关证据，应在 `overall_summary` 中标注为“潜在幻觉”。

## 行为建模逻辑 (Behavioral Modeling)
你需要将 Agent 的轨迹分解为一系列 `AgentBehavior` 单元：
1. **动作还原 (Action)**：Agent 实际上做了什么？（例如：阅读了 A 文件 -> 发现 B 缺失 -> 尝试调用 C 工具）。
2. **结论提取 (Finding)**：Agent 声称发现了什么？
3. **增量判定 (Incremental Check)**：对比前一轮，本轮是否产生了实质性的新认知？如果只是重复之前的分析或道歉，则 `is_incremental = false`。

## 循环判定准则 (Loop Detection)
- **零增量原则**：如果连续 3 轮及以上，`is_incremental` 均为 `false`，且 `action` 模式高度重复（例如：分析 -> 道歉 -> 分析），则必须判定 `is_looping = true`。
- **行为指纹**：识别是否存在“原地打转”的指纹（如：反复读取同一个文件但得不出新结论）。

## 输出要求
输出一个包含 `behavior_sequence`, `reasoning_pattern`, `is_looping`, `loop_analysis`, `overall_summary` 的 JSON 对象。
- `reasoning_pattern` 必须从 [initial_exploration, gap_filling, hypothesis_testing, course_correction] 中选择。
- `loop_analysis`：若循环，详细描述其重复的行为链条。

## 示例
轨迹：["我阅读了 a.py", "我想再次确认 a.py 的内容", "抱歉，我重新阅读 a.py"]
输出：
{{
  "behavior_sequence": [
    {{ "action": "阅读 a.py", "finding": "了解了基础结构", "is_incremental": true }},
    {{ "action": "重复阅读 a.py", "finding": "无新发现", "is_incremental": false }},
    {{ "action": "再次阅读 a.py", "finding": "无新发现", "is_incremental": false }}
  ],
  "reasoning_pattern": "initial_exploration",
  "is_looping": true,
  "loop_analysis": "Agent 陷入了重复读取同一文件的死循环，未能向下一阶段推进。",
  "overall_summary": "Agent 在初步探索阶段陷入循环。"
}}
""",
                ),
                llm_config=self.llm_config,
            ),
            UserStep(
                name="DialogueSensor",
                schema=StepSchema(
                    input_keys=["feedbacks", "agent_context", "file_context"],
                    output_key="dialogue_context",
                    output_type=DialogueContext,
                    prompt_template="""你是一个心理意图分析师。分析用户与 Agent 的对话历史，提取当前真实的心理状态和意图。

## 输入
- 用户反馈历史(feedbacks)：{feedbacks}
- Agent 行为分析(agent_context)：{agent_context}
- 物理文件实证(file_context)：{file_context}

## 输入字段说明
- **feedbacks**: 用户发送给 Agent 的反馈历史列表。
  - 格式：`["反馈 1", "反馈 2", ...]`
  - 顺序：按时间顺序排列。
  - 重要：如果此列表为空 `[]`，则 `has_history` 必须为 `false`。
- **agent_context**: 之前分析的 Agent 行为总结。用于对比“Agent 认为自己做了什么”与“用户感受到了什么”。
- **file_context**: 物理文件实证。用于判断用户在反馈中提到的具体文件/代码是否真实存在。

## 认知分析步骤
1. **意图演进**：分析用户从初始问题到当前反馈，意图发生了怎样的偏移或深化？
2. **情绪量化**：根据关键词和语气，精准判定 `emotional_tone` [satisfied, dissatisfied, confused, urgent, neutral]。
3. **隐性需求挖掘**：用户没明说，但基于当前进展，他实际上在期待什么？（例如：Agent 提供了理论，用户其实在期待代码实现）。

## 输出要求
输出一个包含 `user_intent`, `emotional_tone`, `key_questions`, `implicit_needs`, `has_history`, `summary` 的 JSON 对象。
- `has_history`: 如果 feedbacks 为空，则为 false。
- `emotional_tone`: 必须严格匹配枚举值。

## 示例
反馈：["太慢了", "还是没看到那个文件"]
输出：
{{
  "user_intent": "尽快获取具体的输出文件",
  "emotional_tone": "urgent",
  "key_questions": ["文件到底在哪？"],
  "implicit_needs": ["需要 Agent 停止解释，直接提供结果"],
  "has_history": true,
  "summary": "用户处于焦虑状态，对进度不满，极度渴望看到实证结果。"
}}
""",
                ),
                llm_config=self.llm_config,
            ),
        ]

    async def _extract_evidence(
        self,
        file_path: str,
        file_content: str,
        prompt_template: str,
        question: str,
        llm_client: LLMClientABC,
    ) -> tuple[str, FileEvidence]:
        prompt = prompt_template.format(
            file_path=file_path,
            file_content=file_content,
            question=question,
        )
        # We expect a JSON response here for FileEvidence
        resp = await llm_client.generate(prompt, config=self.llm_config)

        # Remove potential markdown blocks
        cleaned_resp = resp.strip().removeprefix("```json").removesuffix("```").strip()
        evidence = FileEvidence.model_validate_json(cleaned_resp)
        return (file_path, evidence)

    async def execute(
        self,
        data_pool: Dict[str, Any],
        global_context: Dict[str, Any],
        llm_client: LLMClientABC,
    ):
        logger.info("Entering Perception Stage V2 (Evidence-Driven)...")

        # 1. FileSensor: Extract evidence from each file in parallel
        file_sensor_step = self.steps[0]
        file_contents: Dict[str, str] = global_context.get("file_contents", {})

        all_evidences = []
        if file_contents:
            logger.info(f"Extracting evidence from {len(file_contents)} files...")
            tasks = [
                self._extract_evidence(
                    path,
                    content,
                    file_sensor_step.schema.prompt_template,
                    global_context["question"],
                    llm_client,
                )
                for path, content in file_contents.items()
            ]
            results = await asyncio.gather(*tasks)
            all_evidences = [ev for _, ev in results]

            # Synthesize the overall file_context summary (simple LLM call or join)
            summary_prompt = f"Based on the following evidences: {all_evidences}, provide a 1-sentence summary of the workspace state."
            summary = await llm_client.generate(summary_prompt, config=self.llm_config)
            data_pool[file_sensor_step.schema.output_key] = FileContext(
                evidences=all_evidences, summary=summary
            ).model_dump()
        else:
            data_pool[file_sensor_step.schema.output_key] = FileContext(
                evidences=[], summary="No files available."
            ).model_dump()

        # 2. Execute remaining sensors (AgentSensor, DialogueSensor)
        for step in self.steps[1:]:
            res = await step.execute(data_pool, global_context, llm_client)
            # In v2, the execute method of UserStep should return the parsed model
            data_pool[step.schema.output_key] = res["json_resp"]
