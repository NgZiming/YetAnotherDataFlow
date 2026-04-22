from typing import Union, TypeAlias
from dataflow.core.operator import OperatorABC
from dataflow.core.wrapper import WrapperABC
from dataflow.core.llm_serving import LLMServingABC
from dataflow.core.llm_serving import AgentServingABC

# 定义类型别名
OPERATOR_CLASSES: TypeAlias = Union[OperatorABC, WrapperABC]
LLM_SERVING_CLASSES: TypeAlias = Union[LLMServingABC, AgentServingABC]
