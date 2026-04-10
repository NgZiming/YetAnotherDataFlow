"""
cli_openclaw_serving 冒烟测试

使用真实测试数据验证核心功能，包含完整的 file_contents 数据。
"""

import json
import pytest
from dataflow.serving import create_openclaw_serving


# 真实测试数据（包含完整的 file_contents）
TEST_DATA = [
    {
        "task_id": "row-d069b54b-0154-4a0a-9b1a-cc70bfafa445",
        "question": "我正在启动一个关于'Transformer 架构在长文本处理中的优化'的开源项目。请先帮我搜索 arXiv 上 2024 年发布的、标题中包含'Transformer'且摘要涉及'long context'或'length extrapolation'的最新 5 篇论文，提取它们的标题、作者和核心创新点摘要。然后，基于这 5 篇论文的调研结果，为我生成一份标准的 GitHub README.md 文件，要求包含项目背景（引用上述论文的研究动机）、相关工作介绍以及预期的技术路线图。",
        "target_skills": ["arxiv-search-master", "docs-generator"],
        "file_contents": {},
    },
    {
        "task_id": "row-8f91fe40-0532-49d7-8927-db6d7c715900",
        "question": "我需要处理一份位于 /workspace/legal/contracts/2024_scan.pdf 的扫描件合同。请先利用 OCR 技术将这张图片转换为可搜索的文本层，然后在生成的新文件中，于每一页右下角添加红色的'内部绝密'中文水印，并设置透明度为 30%，最后将处理好的文件保存为 /workspace/legal/contracts/2024_processed.pdf。",
        "target_skills": ["docs-pdf", "pdf-watermark-chinese"],
        "file_contents": {
            "/workspace/assets/2024_scan.pdf": {
                "filename": "/workspace/assets/2024_scan.pdf",
                "format": "pdf",
                "title": "2024 年度战略合作协议扫描件（OCR 处理后）",
                "sections": [
                    {
                        "heading": "一、合同基本信息",
                        "paragraphs": [
                            "本合同编号为：LEG-2024-SCN-001，签署日期为 2024 年 3 月 15 日。",
                            "甲方：未来科技集团有限公司，乙方：云端数据服务有限公司。",
                            "经双方友好协商，就 2024 年度数据服务合作达成如下协议。",
                        ],
                    },
                    {
                        "heading": "二、合作内容与范围",
                        "paragraphs": [
                            "乙方将为甲方提供全天候的数据存储与计算服务，确保数据安全性与可用性达到 99.9%。",
                            "服务范围涵盖数据备份、灾难恢复及实时数据分析支持。",
                            "双方约定每季度进行一次服务评估会议，以优化合作流程。",
                        ],
                    },
                    {
                        "heading": "三、费用与支付方式",
                        "paragraphs": [
                            "年度服务总费用为人民币伍佰万元整（¥5,000,000）。",
                            "支付方式分为四期，每期于季度末前 10 个工作日支付。",
                            "若发生额外服务需求，费用将另行协商并签署补充协议。",
                        ],
                    },
                ],
                "table": {
                    "headers": ["服务项目", "单价（元/月）", "备注"],
                    "rows": [
                        ["基础存储服务", "80,000", "包含 100TB 存储空间"],
                        ["计算资源租赁", "120,000", "按实际使用量计费"],
                        ["安全审计服务", "50,000", "每月一次全面审计"],
                    ],
                },
            },
            "/workspace/assets/2024_processed.pdf": {
                "filename": "/workspace/assets/2024_processed.pdf",
                "format": "pdf",
                "title": "2024 年度技术服务合同（扫描件 OCR 处理版）",
                "sections": [
                    {
                        "heading": "一、合同基本信息",
                        "paragraphs": [
                            "本合同由甲方（委托方）与乙方（服务方）于 2024 年 3 月 15 日签署，旨在明确双方在技术服务项目中的权利与义务。",
                            "合同编号：TS-2024-0892，有效期为自签署之日起至 2025 年 3 月 14 日止。",
                        ],
                    },
                    {
                        "heading": "二、服务范围与内容",
                        "paragraphs": [
                            "乙方需为甲方提供企业级数据迁移与系统优化服务，包括但不限于数据库架构调整、API 接口重构及性能调优。",
                            "所有交付物需符合 ISO 27001 信息安全标准，并通过第三方安全审计。",
                        ],
                    },
                    {
                        "heading": "三、费用与支付方式",
                        "paragraphs": [
                            "本合同总金额为人民币壹佰贰拾万元整（¥1,200,000.00），分三期支付。",
                            "首期款于合同签订后 5 个工作日内支付 40%，中期款在项目验收合格后支付 40%，尾款在质保期结束后支付 20%。",
                        ],
                    },
                ],
                "table": {
                    "headers": ["阶段", "交付内容", "预计完成时间"],
                    "rows": [
                        ["第一阶段", "需求分析与方案设计", "2024-04-30"],
                        ["第二阶段", "系统开发与测试", "2024-09-30"],
                        ["第三阶段", "上线部署与培训", "2024-11-15"],
                    ],
                },
            },
        },
    },
    {
        "task_id": "row-17f899ce-6892-4488-b1c6-e917ff8dd9d0",
        "question": "我怀疑公司服务器可能遭受了来自以太坊地址 0x742d35Cc6634C0532925a3b844Bc454e4438f44e 的攻击，请帮我先分析该地址的风险评分、当前持仓及是否有混币器关联记录。如果确认该地址为高风险，请立即对我的 Linux 服务器执行全面安全扫描，重点检查是否存在可疑的高 CPU 占用进程、未授权的开放端口以及配置文件中的 API 密钥泄露情况。",
        "target_skills": ["gate-info-address-tracker", "security-monitor-v15-t33"],
        "file_contents": {},
    },
    {
        "task_id": "row-2047c5a8-3802-4d74-8286-4deeb0477ec3",
        "question": "我正在为一家名为'云图科技'的 SaaS 初创公司制定 Q3 的增长策略，目标是实现产品驱动增长 (PLG)。请首先利用首席营销官顾问技能，模拟在投入 50 万元营销预算下，采用 PLG 模型与 SLG 模型的 MRR 增长轨迹对比，并给出最优预算分配建议。接着，为了评估我们计划重点投放的 A 股科技板块（如代码 688111 的华大九天）的市场热度，请调用威科夫 A 股分析工具，分析该股当前的量价行为结构及趋势阶段，判断是否处于吸筹区间。最后，请将生成的'Q3 PLG 预算分配方案'和'华大九天市场分析报告'作为两项高优先级待办事项，添加到我的任务看板中，并标记为'进行中'状态，以便我本周跟进。",
        "target_skills": ["cmo-advisor", "wyckof-a-share", "flowdo"],
        "file_contents": {},
    },
    {
        "task_id": "row-076c3282-e0eb-40ce-8b8d-8351fae2c45c",
        "question": "我想写一篇关于'2024 年夏季露营装备避坑指南'的小红书爆款笔记。请帮我先搜索小红书上最近一个月关于'露营装备'的高热度笔记，提取用户吐槽最多的 3 个痛点（如帐篷漏水、蚊虫问题等），然后基于这些真实痛点构思一篇结构严谨的避雷文章大纲，最后生成一篇包含吸睛标题、丰富 Emoji 表情和热门标签的小红书风格正文，要求语气亲切且干货满满。",
        "target_skills": [
            "xiaohongshu-search-summarizer",
            "idea-to-post",
            "content-creator-cn",
        ],
        "file_contents": {},
    },
    {
        "task_id": "row-3c9b7b81-07c3-42cf-aac9-3dddb4b08df4",
        "question": "我正在开发一个面向国际买家的中国美妆代工厂筛选平台。首先，请帮我查询位于珠三角地区、专注于护肤品生产的制造集群信息，并提取供应商评估的关键指标（如认证要求、起订量 MOQ）。接着，基于这些业务规则，为我生成一套 TypeScript 接口定义（Interface），包含工厂名称、地理位置、擅长品类、认证列表（ISO/GMP 等）、最小起订量数值以及生产周期字段，确保类型安全以支持后续的数据验证功能。",
        "target_skills": ["china-beauty-factory", "typescript-coder"],
        "file_contents": {},
    },
    {
        "task_id": "row-21a8f59a-4ef5-4e48-8edb-5eeb8ae9a14c",
        "question": "请帮我读取这篇关于'2024 年新能源汽车电池回收政策'的微信公众号文章（链接：https://mp.weixin.qq.com/s/xyz123abc），提取其中的核心观点和关键数据。然后，结合我本地工作区 `/workspace/reports/2023_battery_policy.pdf` 中的历史政策文件，对比分析新政策在'回收责任主体'和'补贴标准'方面的主要变化，并生成一份包含引用来源的对比简报。",
        "target_skills": ["wechat-article-reader-0", "local-data-ai"],
        "file_contents": {
            "/workspace/assets/2023_battery_policy.pdf": {
                "filename": "/workspace/assets/2023_battery_policy.pdf",
                "format": "pdf",
                "title": "2024 年新能源汽车电池回收政策对比分析简报",
                "sections": [
                    {
                        "heading": "一、背景概述",
                        "paragraphs": [
                            "随着 2024 年新能源汽车保有量的激增，电池回收问题日益凸显。本文档基于微信公众号文章《2024 年新能源汽车电池回收政策深度解读》及本地历史文件《2023_battery_policy.pdf》，对新旧政策在关键领域的变化进行对比分析。",
                            "新政策旨在解决旧体系中责任界定模糊和补贴激励不足的问题，构建全生命周期的绿色闭环管理体系。",
                        ],
                    },
                    {
                        "heading": "二、核心观点：回收责任主体的重构",
                        "paragraphs": [
                            "在 2023 年政策中，回收责任主要由第三方回收企业承担，车企参与度较低，导致溯源困难。",
                            "2024 年新政策明确确立了'生产者责任延伸制度（EPR）'，强制要求汽车生产企业作为第一责任人，必须建立逆向物流体系，对退役电池进行全流程追踪，并不得将回收义务完全转嫁给下游。",
                        ],
                    },
                    {
                        "heading": "三、关键数据：补贴标准的动态调整",
                        "paragraphs": [
                            "历史数据显示，2023 年的补贴标准较为固定，未能有效覆盖高成本梯次利用场景。",
                            "新政策引入了'基于再生材料利用率'的动态补贴机制。对于再生钴、镍等关键金属回收率超过 95% 的企业，补贴额度将在基准价基础上上浮 20%，同时取消了针对低效拆解企业的固定补贴。",
                        ],
                    },
                ],
                "table": {
                    "headers": [
                        "对比维度",
                        "2023 年政策 (历史)",
                        "2024 年政策 (新)",
                        "主要变化点",
                    ],
                    "rows": [
                        [
                            "回收责任主体",
                            "以第三方回收企业为主，车企配合度低",
                            "确立汽车生产企业为第一责任人 (EPR)",
                            "责任主体从'下游'前移至'上游'，强化源头管控",
                        ],
                        [
                            "补贴计算方式",
                            "按回收吨数给予固定定额补贴",
                            "按再生材料利用率实施阶梯式动态补贴",
                            "从'重数量'转向'重质量'，鼓励高值化利用",
                        ],
                        [
                            "最高补贴标准",
                            "800 元/吨 (固定)",
                            "基准 1000 元/吨 + 最高 20% 浮动奖励",
                            "潜在补贴上限提升至 1200 元/吨",
                        ],
                        [
                            "溯源管理要求",
                            "仅要求基础台账记录",
                            "强制接入国家动力电池溯源管理平台",
                            "实现全生命周期数字化实时监控",
                        ],
                    ],
                },
            }
        },
    },
    {
        "task_id": "row-a241172d-397b-4ebc-9235-9fd726b4a729",
        "question": "我正在推广一款售价为 199 元的男士运动护膝，目标市场为美国西部。该商品单件重量 0.4kg，尺寸为 20x15x5cm。如果我选择使用 DHL 物流（费率表已配置），且预计退货率为 8%，包材成本约为 5 元/单。请帮我先计算这笔订单的真实履约总成本，然后基于此成本结构，假设我的产品毛利为 60%，帮我算出盈亏平衡的 ROAS 是多少？如果目前的广告名义 ROAS 是 3.5，我应该扩大投放还是削减预算？",
        "target_skills": ["shipping-cost-calculator-ecommerce", "roas-calculator"],
        "file_contents": {},
    },
]


def _convert_file_contents_to_data(file_contents: dict) -> dict:
    """将 file_contents 转换为 input_files_data 格式"""
    if not file_contents:
        return {}

    result = {}
    for path, content in file_contents.items():
        # 提取文件名
        filename = path.split("/")[-1]
        result[filename] = content
    return result


class TestSmoke:
    """冒烟测试"""

    def test_single_complex_query(self):
        """测试单个复杂查询（Transformer 论文调研 + README 生成）"""
        serving = create_openclaw_serving(
            agent_id="main",
            max_workers=2,
            skill_base_dir="/root/clawhub/",
            verification_api_key="EMPTY",
            verification_base_url="http://app-ea48cac8b22348a483d104afbf5f2c65.ns-devsft-3460edd0.svc.cluster.local:8000/v1/",
        )

        task = TEST_DATA[0]
        result = serving.generate_from_input(
            user_inputs=[task["question"]],
            input_files_data=[_convert_file_contents_to_data(task["file_contents"])],
            input_skills_data=[task["target_skills"]],
        )

        assert len(result) == 1
        assert result[0] != ""
        # 解析 JSON 格式返回
        data = json.loads(result[0])
        messages = data.get("messages", [])
        # 提取最后一条 assistant 消息
        output = ""
        for m in reversed(messages):
            if m.get("message", {}).get("role") == "assistant":
                content_list = m.get("message", {}).get("content", [])
                content_parts = []
                for item in content_list:
                    if isinstance(item, dict) and item.get("type") == "text":
                        content_parts.append(item.get("text", ""))
                output = "\n\n".join(content_parts)
                break
        print(f"\n[任务 {task['task_id'][:20]}...] 响应长度：{len(output)} 字符")
        if output:
            print(f"\n完整回复:\n{output}")

    def test_concurrent_multi_skill_tasks(self):
        """测试多任务并发（涉及多个技能协作）"""
        serving = create_openclaw_serving(
            agent_id="main",
            max_workers=4,
            skill_base_dir="/root/clawhub/",
            verification_api_key="EMPTY",
            verification_base_url="http://app-ea48cac8b22348a483d104afbf5f2c65.ns-devsft-3460edd0.svc.cluster.local:8000/v1/",
        )

        tasks = TEST_DATA[:4]
        queries = [t["question"] for t in tasks]
        skills = [t["target_skills"] for t in tasks]
        files = [_convert_file_contents_to_data(t["file_contents"]) for t in tasks]

        result = serving.generate_from_input(
            user_inputs=queries,
            input_files_data=files,
            input_skills_data=skills,
        )

        assert len(result) == 4
        success_count = sum(1 for r in result if r)
        print(f"\n[并发测试] 成功 {success_count} / {len(result)}")
        for i, (task, resp) in enumerate(zip(tasks, result)):
            status = "✓" if resp else "✗"
            # 解析 JSON
            output = ""
            if resp:
                try:
                    data = json.loads(resp)
                    messages = data.get("messages", [])
                    for m in reversed(messages):
                        if m.get("message", {}).get("role") == "assistant":
                            content_list = m.get("message", {}).get("content", [])
                            content_parts = []
                            for item in content_list:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    content_parts.append(item.get("text", ""))
                            output = "\n\n".join(content_parts)
                            break
                except:
                    output = resp
            print(
                f"  {status} {task['task_id'][:20]}... ({len(output)} 字符)"
            )
        # 展示最后一个 agent 的回复
        if result[-1]:
            try:
                data = json.loads(result[-1])
                messages = data.get("messages", [])
                for m in reversed(messages):
                    if m.get("message", {}).get("role") == "assistant":
                        content_list = m.get("message", {}).get("content", [])
                        content_parts = []
                        for item in content_list:
                            if isinstance(item, dict) and item.get("type") == "text":
                                content_parts.append(item.get("text", ""))
                        output = "\n\n".join(content_parts)
                        break
                print(f"\n最后一个 agent 回复:\n{output}")
            except:
                pass

    def test_with_file_assets(self):
        """测试带文件资产的任务（PDF OCR + 水印）"""
        serving = create_openclaw_serving(
            agent_id="main",
            max_workers=2,
            skill_base_dir="/root/clawhub/",
            verification_api_key="EMPTY",
            verification_base_url="http://app-ea48cac8b22348a483d104afbf5f2c65.ns-devsft-3460edd0.svc.cluster.local:8000/v1/",
        )

        task = TEST_DATA[1]  # PDF 处理任务
        result = serving.generate_from_input(
            user_inputs=[task["question"]],
            input_files_data=[_convert_file_contents_to_data(task["file_contents"])],
            input_skills_data=[task["target_skills"]],
        )

        assert len(result) == 1
        # 解析 JSON
        output = ""
        if result[0]:
            try:
                data = json.loads(result[0])
                messages = data.get("messages", [])
                for m in reversed(messages):
                    if m.get("message", {}).get("role") == "assistant":
                        content_list = m.get("message", {}).get("content", [])
                        content_parts = []
                        for item in content_list:
                            if isinstance(item, dict) and item.get("type") == "text":
                                content_parts.append(item.get("text", ""))
                        output = "\n\n".join(content_parts)
                        break
            except:
                output = result[0]
        print(
            f"\n[文件任务 {task['task_id'][:20]}...] 响应：{len(output)} 字符"
        )
        if output:
            print(f"\n完整回复:\n{output}")

    def test_with_file_assets_battery_policy(self):
        """测试带文件资产的任务（电池政策对比分析）"""
        serving = create_openclaw_serving(
            agent_id="main",
            max_workers=2,
            skill_base_dir="/root/clawhub/",
            verification_api_key="EMPTY",
            verification_base_url="http://app-ea48cac8b22348a483d104afbf5f2c65.ns-devsft-3460edd0.svc.cluster.local:8000/v1/",
        )

        task = TEST_DATA[6]  # 电池政策对比任务
        result = serving.generate_from_input(
            user_inputs=[task["question"]],
            input_files_data=[_convert_file_contents_to_data(task["file_contents"])],
            input_skills_data=[task["target_skills"]],
        )

        assert len(result) == 1
        # 解析 JSON
        output = ""
        if result[0]:
            try:
                data = json.loads(result[0])
                messages = data.get("messages", [])
                for m in reversed(messages):
                    if m.get("message", {}).get("role") == "assistant":
                        content_list = m.get("message", {}).get("content", [])
                        content_parts = []
                        for item in content_list:
                            if isinstance(item, dict) and item.get("type") == "text":
                                content_parts.append(item.get("text", ""))
                        output = "\n\n".join(content_parts)
                        break
            except:
                output = result[0]
        print(
            f"\n[政策对比任务 {task['task_id'][:20]}...] 响应：{len(output)} 字符"
        )
        if output:
            print(f"\n完整回复:\n{output}")

    def test_with_verification(self):
        """测试带验证循环的复杂任务"""
        serving = create_openclaw_serving(
            agent_id="main",
            max_workers=2,
            skill_base_dir="/root/clawhub/",
            verification_api_key="EMPTY",
            verification_base_url="http://app-ea48cac8b22348a483d104afbf5f2c65.ns-devsft-3460edd0.svc.cluster.local:8000/v1/",
            verification_client_params={
                "model": "/data/share/models/Qwen3.5-122B-A10B/",
                "max_completion_tokens": 4096,
                "temperature": 0.3,
            },
        )

        task = TEST_DATA[3]  # 复杂多步骤任务（增长策略）
        result = serving.generate_from_input(
            user_inputs=[task["question"]],
            input_files_data=[_convert_file_contents_to_data(task["file_contents"])],
            input_skills_data=[task["target_skills"]],
            enable_verification=True,
            max_verification_rounds=2,
        )

        assert len(result) == 1
        # 解析 JSON
        output = ""
        if result[0]:
            try:
                data = json.loads(result[0])
                messages = data.get("messages", [])
                for m in reversed(messages):
                    if m.get("message", {}).get("role") == "assistant":
                        content_list = m.get("message", {}).get("content", [])
                        content_parts = []
                        for item in content_list:
                            if isinstance(item, dict) and item.get("type") == "text":
                                content_parts.append(item.get("text", ""))
                        output = "\n\n".join(content_parts)
                        break
            except:
                output = result[0]
        print(
            f"\n[验证任务 {task['task_id'][:20]}...] 响应长度：{len(output)} 字符"
        )
        if output:
            print(f"\n完整回复:\n{output}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
