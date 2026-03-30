"""
Pipeline Configuration Loader - 简化版（单 DataSource + 单 Storage）

从 YAML 配置文件加载并动态构建 dataflow 管道。

简化设计：
- 只有一个 DataSource（直接配置，无需命名）
- 只有一个 Storage（直接配置，无需引用）
- 自动调用 split_input() 和 load_partition()
"""

import os
import re
import importlib
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional
import yaml

from dataflow.utils.registry import OPERATOR_REGISTRY


# ==================== 组件类型注册表 ====================

DATA_SOURCE_REGISTRY = {
    "S3DataSource": ("dataflow.utils.storage", "S3DataSource"),
    "FileDataSource": ("dataflow.utils.storage", "FileDataSource"),
    "HuggingFaceDataSource": ("dataflow.utils.storage", "HuggingFaceDataSource"),
    "ModelScopeDataSource": ("dataflow.utils.storage", "ModelScopeDataSource"),
}

STORAGE_REGISTRY = {
    "S3Storage": ("dataflow.utils.storage", "S3Storage"),
    "FileStorage": ("dataflow.utils.storage", "FileStorage"),
}

MEDIA_STORAGE_REGISTRY = {
    "S3MediaStorage": ("dataflow.utils.storage", "S3MediaStorage"),
    "FileMediaStorage": ("dataflow.utils.storage", "FileMediaStorage"),
}

CACHE_STORAGE_REGISTRY = {
    "S3CacheStorage": ("dataflow.utils.storage", "S3CacheStorage"),
    "FileCacheStorage": ("dataflow.utils.storage", "FileCacheStorage"),
}

SERVING_REGISTRY = {
    "APILLMServing_request": (
        "dataflow.serving.api_llm_serving_request",
        "APILLMServing_request",
    ),
    "APIVLMServing_openai": (
        "dataflow.serving.api_vlm_serving_openai",
        "APIVLMServing_openai",
    ),
}

PROMPT_REGISTRY = {
    "FormatStrPrompt": ("dataflow.prompts.core_text", "FormatStrPrompt"),
    "CodeQualityEvaluatorPrompt": ("dataflow.prompts.code", "CodeQualityEvaluatorPrompt"),
    "CodeCodeToInstructionGeneratorPrompt": ("dataflow.prompts.code", "CodeCodeToInstructionGeneratorPrompt"),
    "CodeInstructionGeneratePrompt": ("dataflow.prompts.code", "CodeInstructionGeneratePrompt"),
    "CodeInstructionEnhancement": ("dataflow.prompts.code", "CodeInstructionEnhancement"),
    "CodeInstructionToCodeGeneratorPrompt": ("dataflow.prompts.code", "CodeInstructionToCodeGeneratorPrompt"),
    "DiyCodePrompt": ("dataflow.prompts.code", "DiyCodePrompt"),
    "KnowledgeCleanerPrompt": ("dataflow.prompts.kbcleaning", "KnowledgeCleanerPrompt"),
    "MathbookQuestionExtractPrompt": ("dataflow.prompts.kbcleaning", "MathbookQuestionExtractPrompt"),
    "ExtractScenarioPrompt": ("dataflow.prompts.func_call", "ExtractScenarioPrompt"),
    "ExpandScenarioPrompt": ("dataflow.prompts.func_call", "ExpandScenarioPrompt"),
    "FuncAtomicTaskGeneratePrompt": ("dataflow.prompts.func_call", "FuncAtomicTaskGeneratePrompt"),
    "SequentialTaskGeneratePrompt": ("dataflow.prompts.func_call", "SequentialTaskGeneratePrompt"),
    "ParathenSeqTaskGeneratePrompt": ("dataflow.prompts.func_call", "ParathenSeqTaskGeneratePrompt"),
    "CompositionTaskFilterPrompt": ("dataflow.prompts.func_call", "CompositionTaskFilterPrompt"),
    "FuncGeneratePrompt": ("dataflow.prompts.func_call", "FuncGeneratePrompt"),
    "ConversationUserPrompt": ("dataflow.prompts.func_call", "ConversationUserPrompt"),
    "ConversationAssistantPrompt": ("dataflow.prompts.func_call", "ConversationAssistantPrompt"),
    "ConversationToolPrompt": ("dataflow.prompts.func_call", "ConversationToolPrompt"),
    "ConversationEvalPrompt": ("dataflow.prompts.func_call", "ConversationEvalPrompt"),
    "DiyAnswerGeneratorPrompt": ("dataflow.prompts.reasoning.diy", "DiyAnswerGeneratorPrompt"),
    "DiyQuestionFilterPrompt": ("dataflow.prompts.reasoning.diy", "DiyQuestionFilterPrompt"),
    "DiyQuestionSynthesisPrompt": ("dataflow.prompts.reasoning.diy", "DiyQuestionSynthesisPrompt"),
    "GeneralAnswerGeneratorPrompt": ("dataflow.prompts.reasoning.general", "GeneralAnswerGeneratorPrompt"),
    "GeneralQuestionSynthesisPrompt": ("dataflow.prompts.reasoning.general", "GeneralQuestionSynthesisPrompt"),
    "GeneralQuestionFilterPrompt": ("dataflow.prompts.reasoning.general", "GeneralQuestionFilterPrompt"),
    "MathAnswerGeneratorPrompt": ("dataflow.prompts.reasoning.math", "MathAnswerGeneratorPrompt"),
    "MathQuestionSynthesisPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionSynthesisPrompt"),
    "MathQuestionCategoryPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionCategoryPrompt"),
    "MathQuestionDifficultyPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionDifficultyPrompt"),
    "MathQuestionFilterPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionFilterPrompt"),
    "MathQuestionSequentialFusionGeneratorPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionSequentialFusionGeneratorPrompt"),
    "MathQuestionParallelFusionGeneratorPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionParallelFusionGeneratorPrompt"),
    "MathQuestionConditionFusionGeneratorPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionConditionFusionGeneratorPrompt"),
    "MathQuestionEvaluatorPrompt": ("dataflow.prompts.reasoning.math", "MathQuestionEvaluatorPrompt"),
    "Text2SQLCorrespondenceFilterPrompt": ("dataflow.prompts.text2sql", "Text2SQLCorrespondenceFilterPrompt"),
    "Text2SQLCotGeneratorPrompt": ("dataflow.prompts.text2sql", "Text2SQLCotGeneratorPrompt"),
    "SelectSQLGeneratorPrompt": ("dataflow.prompts.text2sql", "SelectSQLGeneratorPrompt"),
    "SelectVecSQLGeneratorPrompt": ("dataflow.prompts.text2sql", "SelectVecSQLGeneratorPrompt"),
    "Text2SQLQuestionGeneratorPrompt": ("dataflow.prompts.text2sql", "Text2SQLQuestionGeneratorPrompt"),
    "Text2VecSQLQuestionGeneratorPrompt": ("dataflow.prompts.text2sql", "Text2VecSQLQuestionGeneratorPrompt"),
    "SQLVariationGeneratorPrompt": ("dataflow.prompts.text2sql", "SQLVariationGeneratorPrompt"),
    "Text2SQLPromptGeneratorPrompt": ("dataflow.prompts.text2sql", "Text2SQLPromptGeneratorPrompt"),
    "Text2VecSQLPromptGeneratorPrompt": ("dataflow.prompts.text2sql", "Text2VecSQLPromptGeneratorPrompt"),
}

PIPELINE_REGISTRY = {
    "PartitionPipelineParallelRun": ("dataflow.pipeline.Pipeline", "PartitionPipelineParallelRun"),
    "PipelineABC": ("dataflow.pipeline.Pipeline", "PipelineABC"),
}


# ==================== 配置数据类 ====================


@dataclass
class DataSourceConfig:
    type: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StorageConfig:
    type: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaStorageConfig:
    type: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ServingConfig:
    type: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OperatorKeyConfig:
    input: Dict[str, str] = field(default_factory=dict)
    output: Dict[str, str] = field(default_factory=dict)


@dataclass
class OperatorConfig:
    type: str = ""
    serving: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    keys: OperatorKeyConfig = field(default_factory=OperatorKeyConfig)


@dataclass
class PipelineExecutionConfig:
    partitions: int = 1
    max_parallelism: int = 4


@dataclass
class PipelineConfig:
    metadata: Dict[str, Any] = field(default_factory=dict)
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    media_storage: Optional[MediaStorageConfig] = None
    serving: Dict[str, ServingConfig] = field(default_factory=dict)
    operators: Dict[str, OperatorConfig] = field(default_factory=dict)
    pipeline_type: str = "PartitionPipelineParallelRun"
    steps: List[str] = field(default_factory=list)
    execution: PipelineExecutionConfig = field(default_factory=PipelineExecutionConfig)

    _data_source: Any = field(default=None, repr=False)
    _storage: Any = field(default=None, repr=False)
    _media_storage: Any = field(default=None, repr=False)
    _serving_map: Dict[str, Any] = field(default_factory=dict, repr=False)
    _operator_map: Dict[str, Any] = field(default_factory=dict, repr=False)


# ==================== 配置解析器 ====================


class ConfigResolver:
    @staticmethod
    def resolve(value: Any, context: Dict[str, Any]) -> Any:
        if isinstance(value, str):
            value = re.sub(
                r"\$\{([^}:]+)(?::([^}]*))?\}",
                lambda m: os.environ.get(m.group(1), m.group(2) or ""),
                value,
            )
            value = re.sub(
                r"\$\{\{([^}]+)\}\}",
                lambda m: ConfigResolver._get_nested(context, m.group(1)),
                value,
            )
            return value

        elif is_dataclass(value) and not isinstance(value, type):
            for f in fields(value):
                if f.name.startswith("_"):
                    continue
                field_value = getattr(value, f.name)
                resolved = ConfigResolver.resolve(field_value, context)
                if is_dataclass(resolved) and not isinstance(resolved, type):
                    ConfigResolver.resolve(resolved, context)
                setattr(value, f.name, resolved)
            return value

        elif isinstance(value, dict):
            return {k: ConfigResolver.resolve(v, context) for k, v in value.items()}

        elif isinstance(value, list):
            return [ConfigResolver.resolve(item, context) for item in value]

        return value

    @staticmethod
    def _get_nested(context: Dict[str, Any], path: str) -> str:
        keys = path.split(".")
        value = context
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key, "")
            else:
                return ""
        return str(value) if value else ""


# ==================== 工厂类 ====================


class ComponentFactory:
    @staticmethod
    def import_class(module_path: str, class_name: str):
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

    @staticmethod
    def create_data_source(config: DataSourceConfig) -> Any:
        if config.type not in DATA_SOURCE_REGISTRY:
            raise ValueError(f"Unsupported data_source type: {config.type}. Supported: {list(DATA_SOURCE_REGISTRY.keys())}")

        module_path, class_name = DATA_SOURCE_REGISTRY[config.type]
        ds_class = ComponentFactory.import_class(module_path, class_name)

        if config.type == "S3DataSource":
            params = {
                "endpoint": config.params.get("endpoint"),
                "ak": config.params.get("ak"),
                "sk": config.params.get("sk"),
                "s3_paths": config.params.get("s3_paths"),
                "format_type": config.params.get("format_type", "jsonl"),
            }
        elif config.type == "FileDataSource":
            params = {
                "paths": config.params.get("paths"),
                "format_type": config.params.get("format_type", "jsonl"),
            }
        elif config.type == "HuggingFaceDataSource":
            params = {
                "dataset": config.params.get("dataset"),
                "split": config.params.get("split", "train"),
                "streaming": config.params.get("streaming", True),
            }
        elif config.type == "ModelScopeDataSource":
            params = {
                "dataset": config.params.get("dataset"),
                "split": config.params.get("split", "train"),
            }
        else:
            raise ValueError(f"Unsupported data_source type: {config.type}")

        return ds_class(**params)

    @staticmethod
    def create_storage(config: StorageConfig, data_source: Any) -> Any:
        if config.type not in STORAGE_REGISTRY:
            raise ValueError(f"Unsupported storage type: {config.type}. Supported: {list(STORAGE_REGISTRY.keys())}")

        module_path, class_name = STORAGE_REGISTRY[config.type]
        storage_class = ComponentFactory.import_class(module_path, class_name)

        params = {"data_source": data_source}

        if config.type == "S3Storage":
            params.update({
                "output_s3_path": config.params.get("output_s3_path"),
                "id_key": config.params.get("id_key", "id"),
                "cache_type": config.params.get("cache_type", "jsonl"),
            })
        elif config.type == "FileStorage":
            params.update({
                "cache_path": config.params.get("cache_path", "./cache"),
                "id_key": config.params.get("id_key", "id"),
                "cache_type": config.params.get("cache_type", "jsonl"),
            })

        return storage_class(**params)

    @staticmethod
    def create_media_storage(config: MediaStorageConfig) -> Any:
        if config.type not in MEDIA_STORAGE_REGISTRY:
            raise ValueError(f"Unsupported media_storage type: {config.type}")

        module_path, class_name = MEDIA_STORAGE_REGISTRY[config.type]
        media_storage_class = ComponentFactory.import_class(module_path, class_name)

        if config.type == "S3MediaStorage":
            params = {"endpoint": config.params.get("endpoint"), "ak": config.params.get("ak"), "sk": config.params.get("sk")}
        elif config.type == "FileMediaStorage":
            params = {}
        else:
            raise ValueError(f"Unsupported media_storage type: {config.type}")

        return media_storage_class(**params)

    @staticmethod
    def create_cache_storage(cache_storage_type: str, cache_storage_params: Dict[str, Any]) -> Any:
        if cache_storage_type not in CACHE_STORAGE_REGISTRY:
            raise ValueError(f"Unsupported cache_storage type: {cache_storage_type}")

        module_path, class_name = CACHE_STORAGE_REGISTRY[cache_storage_type]
        cache_storage_class = ComponentFactory.import_class(module_path, class_name)

        if cache_storage_type == "S3CacheStorage":
            params = {
                "endpoint": cache_storage_params.get("endpoint"),
                "ak": cache_storage_params.get("ak"),
                "sk": cache_storage_params.get("sk"),
                "cache_file": cache_storage_params.get("cache_file"),
            }
        elif cache_storage_type == "FileCacheStorage":
            params = {"cache_file": cache_storage_params.get("cache_file", "./cache/progress.json")}
        else:
            raise ValueError(f"Unsupported cache_storage type: {cache_storage_type}")

        return cache_storage_class(**params)

    @staticmethod
    def create_serving(config: ServingConfig, media_storage: Any = None) -> Any:
        if config.type not in SERVING_REGISTRY:
            raise ValueError(f"Unknown serving type: {config.type}")

        module_path, class_name = SERVING_REGISTRY[config.type]
        serving_class = ComponentFactory.import_class(module_path, class_name)

        constructor_params = dict(config.params)
        if media_storage and "media_storage" not in constructor_params:
            constructor_params["media_storage"] = media_storage

        return serving_class(**constructor_params)

    @staticmethod
    def create_operator(config: OperatorConfig, serving_map: Dict[str, Any]) -> Any:
        try:
            operator_class = OPERATOR_REGISTRY.get(config.type)
        except KeyError:
            raise ValueError(f"Unknown operator type: {config.type}")

        constructor_params = dict(config.params)

        if "prompt_template" in constructor_params:
            prompt_config = constructor_params["prompt_template"]
            if isinstance(prompt_config, dict) and "type" in prompt_config:
                if prompt_config["type"] not in PROMPT_REGISTRY:
                    raise ValueError(f"Unknown prompt template type: {prompt_config['type']}")
                module_path, class_name = PROMPT_REGISTRY[prompt_config["type"]]
                prompt_class = ComponentFactory.import_class(module_path, class_name)
                prompt_params = prompt_config.get("params", {})
                constructor_params["prompt_template"] = prompt_class(**prompt_params)

        if config.serving:
            if config.serving not in serving_map:
                raise ValueError(f"Unknown serving reference: {config.serving}")
            constructor_params["llm_serving"] = serving_map[config.serving]

        return operator_class(**constructor_params)


# ==================== 配置加载器 ====================


def class_init(self, cache_storage, partitions, storage, media_storage, steps: list[str], ops: dict[str, Any], op_config, servings):
    super(self.__class__, self).__init__(cache_storage, partitions)
    self.storage = storage
    self.media_storage = media_storage
    self.op_keys = {}

    for step_name in steps:
        if step_name not in ops:
            raise ValueError(f"Unknown operator in steps: {step_name}")
        setattr(self, step_name, ops[step_name])
        self.op_keys[step_name] = op_config[step_name].keys

    for name, serving in servings.items():
        setattr(self, name, serving)


def class_forward(self):
    from dataflow.wrapper.auto_op import AutoOP
    for k, v in vars(self).items():
        if isinstance(v, AutoOP):
            v.run(self.storage.step(), **self.op_keys[k].input, **self.op_keys[k].output)


class PipelineConfigLoader:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config(config_path)

    def _load_config(self, config_path: str) -> PipelineConfig:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        config = PipelineConfig()
        config.metadata = raw_config.get("metadata", {})

        # 简化：直接配置 data_source（单个）
        ds_raw = raw_config.get("data_source", {})
        if ds_raw:
            config.data_source = DataSourceConfig(type=ds_raw["type"], params=ds_raw.get("params", {}))

        # 简化：直接配置 storage（单个）
        storage_raw = raw_config.get("storage", {})
        if storage_raw:
            config.storage = StorageConfig(type=storage_raw["type"], params=storage_raw.get("params", {}))

        # media_storage（可选）
        media_storage_raw = raw_config.get("media_storage", {})
        if media_storage_raw:
            config.media_storage = MediaStorageConfig(type=media_storage_raw["type"], params=media_storage_raw.get("params", {}))

        # serving
        serving_raw = raw_config.get("serving", {})
        for name, s in serving_raw.items():
            config.serving[name] = ServingConfig(type=s["type"], params=s.get("params", {}))

        # operators
        operators_raw = raw_config.get("operators", {})
        for name, o in operators_raw.items():
            keys_raw = o.get("keys", {})
            config.operators[name] = OperatorConfig(
                type=o["type"],
                serving=o.get("serving"),
                params=o.get("params", {}),
                keys=OperatorKeyConfig(input=keys_raw.get("input", {}), output=keys_raw.get("output", {})),
            )

        # pipeline
        pipeline_raw = raw_config.get("pipeline", {})
        config.pipeline_type = pipeline_raw.get("type", "PartitionPipelineParallelRun")
        config.steps = pipeline_raw.get("steps", [])

        # execution
        exec_raw = pipeline_raw.get("execution", {})
        config.execution = PipelineExecutionConfig(
            partitions=exec_raw.get("partitions", 1),
            max_parallelism=exec_raw.get("max_parallelism", 4),
        )

        ConfigResolver.resolve(config, config.__dict__)
        return config

    def build(self) -> Any:
        # 1. 创建 DataSource
        self.config._data_source = ComponentFactory.create_data_source(self.config.data_source)

        # 2. 创建 Storage（传入 data_source）
        self.config._storage = ComponentFactory.create_storage(self.config.storage, self.config._data_source)

        # 3. 创建 MediaStorage（可选）
        if self.config.media_storage:
            self.config._media_storage = ComponentFactory.create_media_storage(self.config.media_storage)

        # 4. 创建 Serving
        for name, serving_config in self.config.serving.items():
            self.config._serving_map[name] = ComponentFactory.create_serving(serving_config, self.config._media_storage)

        # 5. 创建 Operators
        for name, op_config in self.config.operators.items():
            self.config._operator_map[name] = ComponentFactory.create_operator(op_config, self.config._serving_map)

        # 6. 创建 Pipeline
        return self._create_pipeline()

    def _create_pipeline(self) -> Any:
        if self.config.pipeline_type not in PIPELINE_REGISTRY:
            raise ValueError(f"Unsupported pipeline type: {self.config.pipeline_type}")

        module_path, class_name = PIPELINE_REGISTRY[self.config.pipeline_type]
        pipeline_class = ComponentFactory.import_class(module_path, class_name)

        # 自动推断 CacheStorage
        if self.config.storage.type == "S3Storage":
            cache_storage_type = "S3CacheStorage"
            cache_storage_params = {
                "endpoint": self.config.storage.params.get("endpoint"),
                "ak": self.config.storage.params.get("ak"),
                "sk": self.config.storage.params.get("sk"),
                "cache_file": f"{self.config.storage.params.get('output_s3_path', '')}_PROGRESS.txt",
            }
        else:
            cache_storage_type = "FileCacheStorage"
            cache_storage_params = {"cache_file": self.config.storage.params.get("cache_path", "./cache") + "/progress.json"}

        cache_storage = ComponentFactory.create_cache_storage(cache_storage_type, cache_storage_params)

        attr_map = {"__init__": class_init, "forward": class_forward}
        dyn_pipeline_class_impl = type("_DYN_IMPL_PC_", (pipeline_class,), attr_map)

        return dyn_pipeline_class_impl(
            cache_storage,
            self.config.execution.partitions,
            self.config._storage,
            self.config._media_storage,
            self.config.steps,
            self.config._operator_map,
            self.config.operators,
            self.config._serving_map,
        )

    def get_data_source(self) -> Any:
        return self.config._data_source

    def get_storage(self) -> Any:
        return self.config._storage

    def get_media_storage(self) -> Any:
        return self.config._media_storage

    def get_serving(self, name: str) -> Any:
        return self.config._serving_map.get(name)

    def get_operator(self, name: str) -> Any:
        return self.config._operator_map.get(name)

    def get_operator_input_keys(self, operator_name: str) -> Dict[str, str]:
        if operator_name not in self.config.operators:
            return {}
        return self.config.operators[operator_name].keys.input

    def get_operator_output_keys(self, operator_name: str) -> Dict[str, str]:
        if operator_name not in self.config.operators:
            return {}
        return self.config.operators[operator_name].keys.output


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python config_loader.py <config_path>")
        sys.exit(1)

    config_path = sys.argv[1]
    loader = PipelineConfigLoader(config_path)

    print(f"Pipeline: {loader.config.metadata.get('name', 'Unknown')}")
    print(f"Type: {loader.config.pipeline_type}")
    print(f"Steps: {loader.config.steps}")
    print(f"Partitions: {loader.config.execution.partitions}")
    print(f"Max Parallelism: {loader.config.execution.max_parallelism}")
    print()
    print(f"DataSource: {loader.config.data_source.type}, params={loader.config.data_source.params}")
    print(f"Storage: {loader.config.storage.type}, params={loader.config.storage.params}")
    print()
    print("Serving:")
    for name, cfg in loader.config.serving.items():
        print(f"  {name}: {cfg.type}")
    print()
    print("Operators:")
    for name, cfg in loader.config.operators.items():
        print(f"  {name}: {cfg.type}")
