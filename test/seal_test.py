import json

import pandas as pd

from dataflow import get_logger
from dataflow.core import OperatorABC
from dataflow.operators.core_vision import PromptedVQAGenerator
from dataflow.pipeline.Pipeline import StreamBatchedPipelineABC
from dataflow.serving.api_vlm_serving_openai import APIVLMServing_openai
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.s3_plugin import S3JsonlStorage, inject_s3_variables
from dataflow.utils.storage import DataFlowStorage


prompt = """这是一张印章的图片，请提取出印章中的所有有效信息，并输出印章的颜色和类型。

有效信息包括：
1. 印章上的企业单位、政府单位、个人名称、电话号码等身份信息。
2. 印章用途，如：质检、认证、专用场景信息。
3. 印章的数字编号信息。
4. 印章的日期、时效信息。

有效信息不包括：
1. 和印章整体颜色字体不一致的文字内容，如：
    a. 个人签名、手写的日期信息等覆盖在印章上的文字信息。
    b. 底下的合同、正文内容和金额数字等被印章覆盖的文字信息。
2. 印章上的图型信息，如：五角星、三角形、国徽等。

输出要求：
1. 按照阅读顺序输出有效信息，不要包含任何额外的内容。
2. 输出有效信息的原文，不能进行任何的改动或精简。
3. 圆形印章按照从外层到内层的顺序进行阅读，优先输出外层内容。内层文字遵循从上倒下，从左到右的阅读顺序。
4. 输出格式为 json，有效信息输出到 "texts" 字段内，"texts" 字段是一个字符串列表。
5. 每一行有效信息为 "texts" 子段列表中的一个字符串，同一行的内容只需要用空格分开。
6. 如果图片中并不存在印章，请直接输出： ```json
{
    "invalid": "not_seal"
}
```
7. 如果图片过于模糊，无法识别，请直接输出： ```json
{
    "invalid": "blur"
}
```
8. 印章的颜色输出到 color 字段，可选的输出颜色有：["红色", "蓝色", "灰色或黑色", "其他颜色"]
9. 印章的类型输出到 type 字段，可选的输出类型有：["正圆章", "椭圆章", "方形章", "菱形章", "三角章", "其他章"]

下面是输出的一些样例，供你参考：
1. ```json
{
    "texts": [
        "千盛服饰广场",
        "作废 2022-03-20 日期",
        "合约解除专用"
    ],
    "color": "蓝色",
    "type": "方形章"
}
```
2. ```json
{
    "texts": [
        "乔治亚太平洋吉平（上海）贸易有限公司"
    ],
    "color": "灰色或黑色",
    "type": "椭圆章"
}
```
3. ```json
{
    "texts": [
        "玉环市金航阀门配件有限公司",
        "1975510067243",
        "发票专用章",
        "夭揖擦"
    ],
    "color": "红色",
    "type": "正圆章"
}
```"""


@OPERATOR_REGISTRY.register()
class JsonParseFilter(OperatorABC):
    def __init__(self):
        self.logger = get_logger()
        self.logger.info(f"Initializing {self.__class__.__name__}")

    @staticmethod
    def get_desc(lang: str = "zh"):
        return "Json Parse Filter"

    def _validate_dataframe(self, dataframe: pd.DataFrame):
        required_keys = [self.input_key]
        forbidden_keys = []

        missing = [k for k in required_keys if k not in dataframe.columns]
        conflict = [k for k in forbidden_keys if k in dataframe.columns]

        if missing:
            raise ValueError(f"Missing required column(s): {missing}")
        if conflict:
            raise ValueError(
                f"The following column(s) already exist and would be overwritten: {conflict}"
            )

    def run(
        self,
        storage: DataFlowStorage,
        input_key: str,
        output_key: str,
    ) -> str:
        self.input_key = input_key
        self.output_key = output_key

        df: pd.DataFrame = storage.read("dataframe")
        rtn: list[dict] = []

        for x in df.to_dict(orient="records"):
            json_string: str = x[input_key]
            json_string = json_string.removeprefix("```json").removesuffix("```")
            try:
                d = json.loads(json_string)
                x[output_key] = d
                rtn.append(x)
            except:
                pass

        filtered_df = pd.DataFrame(rtn)
        self.logger.info(f"Filtering complete. Remaining rows: {len(filtered_df)}")
        storage.write(filtered_df)
        self.logger.info(
            f"Filtering completed. Total records passing filter: {len(filtered_df)}."
        )
        return output_key


class SealPipeline(StreamBatchedPipelineABC):
    def __init__(self):
        super().__init__()
        self.storage = S3JsonlStorage(
            ["s3://pedia-doc-ai/wuziming/DFT/imgs-samples.jsonl"],
            "s3://pedia-doc-ai/wuziming/DFT-output/",
        )

        self.serving = APIVLMServing_openai(
            api_url="http://app-cae22541ad874411aa1026c89bff7180.ns-devsft-3460edd0.svc.cluster.local:8000/v1",
            model_name="/data/share/models/Qwen3-VL-235B-A22B-Instruct/",
        )

        self.op1 = PromptedVQAGenerator(
            self.serving,
            prompt,
        )

        self.op2 = JsonParseFilter()

    def forward(self):
        self.op1.run(self.storage.step(), "image", "seal_desc_json")
        self.op2.run(self.storage.step(), "seal_desc_json", "parsed_json")


def main():
    inject_s3_variables(
        endpoint="http://aoss-internal.cn-sh-01b.sensecoreapi-oss.cn/",
        ak="81E020BE381B41CEBE7C1034F6AE451A",
        sk="68440A553C9D4DB2AC751852B76E1E51",
    )
    pipeline = SealPipeline()
    pipeline.compile()
    pipeline.forward(
        resume_from_last=False,
        batch_size=4,
    )


if __name__ == "__main__":
    main()
