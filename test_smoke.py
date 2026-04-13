#!/usr/bin/env python3
"""
CLIOpenClawServing 冒烟测试脚本

用于在远端机器上快速验证 OpenClaw Serving 的基本功能。

使用示例:
    python test_smoke.py

或者传入任务 JSON:
    python test_smoke.py --task '{"task_id": "...", "question": "..."}'
"""

import json
import sys
from pathlib import Path
from dataflow.serving.agent.cli_openclaw_serving import CLIOpenClawServing


def create_test_task():
    """创建一个测试任务（用户提供的示例）"""
    return {
        "task_id": "row-55d9d936-994a-4552-9211-5b4e10ec67a5",
        "question": "我正在开发一个基于 Transformer 架构的实时语音识别项目。请帮我先在 arXiv 上搜索 2024 年发布的关于 'Real-time Speech Recognition with Transformers' 的最新论文，下载 PDF 并提取其核心模型架构描述；随后，利用这些技术细节为我生成一份标准的 README.md 文件，要求包含项目简介、核心技术原理（基于论文摘要）、安装步骤以及 API 接口调用示例的占位符。",
        "skills": ["arxiv-search-master", "docs-generator"],
        "file_contents": {
            "/workspace/assets/Real-time_Speech_Recognition_with_Transformers.pdf": {
                "filename": "/workspace/assets/Real-time_Speech_Recognition_with_Transformers.pdf",
                "format": "pdf",
                "title": "Real-time Speech Recognition with Transformers: A 2024 Survey and Architecture Guide",
                "sections": [
                    {
                        "heading": "Abstract",
                        "paragraphs": [
                            "This paper presents a comprehensive survey of real-time speech recognition systems based on Transformer architectures published in 2024. We analyze recent advancements in streaming attention mechanisms, chunked processing strategies, and low-latency decoding algorithms.",
                            "Our core contribution is the 'StreamFormer-X' architecture, which integrates dynamic context windowing with a hybrid CTC-Attention loss function to achieve state-of-the-art Word Error Rate (WER) while maintaining sub-100ms latency on edge devices.",
                        ],
                    },
                    {
                        "heading": "Core Model Architecture",
                        "paragraphs": [
                            "The StreamFormer-X model utilizes a modified Transformer encoder where self-attention is restricted to a sliding window of fixed size, augmented with a lookahead buffer for future context estimation without compromising causality.",
                            "We introduce a novel 'Temporal Compression Module' that reduces sequence length by a factor of 4 before feeding into the deep transformer layers, significantly accelerating inference speed while preserving acoustic feature integrity.",
                        ],
                    },
                    {
                        "heading": "Performance Benchmarks",
                        "paragraphs": [
                            "Experiments conducted on the LibriSpeech and CommonVoice datasets demonstrate that our proposed architecture outperforms existing Conformer-based baselines by 3.5% in relative WER reduction under strict latency constraints.",
                            "The model achieves an average end-to-end latency of 85ms on NVIDIA Jetson Orin hardware, making it suitable for interactive voice assistant applications.",
                        ],
                    },
                ],
                "table": {
                    "headers": [
                        "Model Variant",
                        "Latency (ms)",
                        "WER (%)",
                        "Parameters (M)",
                    ],
                    "rows": [
                        ["StreamFormer-X (Base)", "85", "3.2", "45"],
                        ["StreamFormer-X (Large)", "110", "2.8", "120"],
                        ["Conformer-Streaming", "95", "3.6", "50"],
                        ["Transformer-Transducer", "140", "3.9", "60"],
                    ],
                },
            }
        },
    }


def format_trajectory_result(trajectory: dict) -> str:
    """格式化轨迹结果用于显示"""
    lines = []
    lines.append("=" * 60)
    lines.append("任务结果")
    lines.append("=" * 60)
    lines.append(f"任务 ID: {trajectory.get('task_id', 'N/A')}")
    lines.append(f"是否完成：{'是' if trajectory.get('is_completed') else '否'}")
    lines.append(f"总轮次：{trajectory.get('total_rounds', 0)}")
    lines.append(f"创建的文件：{len(trajectory.get('files_created', []))} 个")
    lines.append(f"错误信息：{len(trajectory.get('errors', []))} 条")
    lines.append("")
    lines.append("-" * 60)
    lines.append("最终输出:")
    lines.append("-" * 60)
    print("\n".join(lines))
    print(trajectory.get("final_output", "无输出"))

    if trajectory.get("errors"):
        print("\n" + "-" * 60)
        print("错误信息:")
        print("-" * 60)
        for error in trajectory["errors"]:
            print(f"  - {error}")

    return trajectory.get("final_output", "")


def run_smoke_test():
    """运行冒烟测试"""
    print("=" * 60)
    print("CLIOpenClawServing 冒烟测试")
    print("=" * 60)
    print()

    # 1. 创建 serving 实例
    print("[1/4] 创建 CLIOpenClawServing 实例...")
    try:
        serving = CLIOpenClawServing(
            agent_id="main",
            model="/data/share/models/Qwen3.5-122B-A10B/",
            max_workers=1,
            max_retries=1,
            skill_base_dir="/root/clawhub",
            verification_base_url="http://app-ea48cac8b22348a483d104afbf5f2c65.ns-devsft-3460edd0.svc.cluster.local:8000/v1/chat/completions",
            verification_api_key="EMPTY",
            verification_client_params={
                "model": "/data/share/models/Qwen3.5-122B-A10B/",
                "max_tokens": 4096,
            },
        )
        print("  ✅ 实例创建成功")
    except Exception as e:
        print(f"  ❌ 实例创建失败：{e}")
        return False

    # 2. 准备测试任务
    print("\n[2/4] 准备测试任务...")
    task = create_test_task()
    print(f"  任务 ID: {task['task_id']}")
    print(f"  使用技能：{', '.join(task['skills'])}")
    print(f"  文件数量：{len(task['file_contents'])}")
    print("  ✅ 任务准备完成")

    # 3. 执行任务
    print("\n[3/4] 执行任务...")
    print("  (这可能需要几分钟，请耐心等待)")
    try:
        # 调用 generate_from_input
        # 注意：generate_from_input 接收的是 [task_dict] 列表
        results = serving.generate_from_input(
            user_inputs=[task["question"]],
            input_files_data=[task["file_contents"]],
            input_skills_data=[task["skills"]],
            enable_verification=True,
            verification_prompt_template="""你是一个真实用户，正在检查 agent 有没有完成你的任务。
请根据以下信息评估任务完成情况，并给出自然、真实的反馈。

---

【你的任务要求】
{task_description}

【Agent 已经做过的步骤】
{agent_outputs}

【之前给过的反馈】（如果有）
{feedbacks}

【Agent 生成的文件】（如果有）
{file_contents}

---

## 你的任务
1. 先快速浏览生成的文件内容，总结关键信息
2. 判断任务是否完成（completed / incomplete）
3. 写一段自然的反馈（像真人聊天一样）

## 反馈风格要求
**✅ 要这样写：**
- 口语化，像微信/聊天软件里发消息
- 简短，1-3 句话就够了
- 可以先总结文件内容，再评价任务完成情况
- 可以有疑问、确认、轻微不满等真实情绪
- 指出具体缺什么，比如"XXX 文件呢？""第二步的搜索结果没看到"
- 表扬也可以，比如"做得不错""这个分析挺到位"

**❌ 不要这样写：**
- 不要太正式："经评估，任务未完成..."
- 不要太长：别写一大段分析
- 不要太客气："抱歉，似乎还有改进空间..."
- 不要泛泛而谈："步骤不完整"（要说具体哪一步）
- 不要只说文件名，要总结内容："README 生成了" → "README 生成了，包含项目简介和安装步骤"

## 反馈示例

✅ 好的例子：
- "等等，README 还没生成吧？补上。"
- "第二步的论文摘要呢？没看到。"
- "README 生成了，包含项目简介、核心原理、安装步骤和 API 示例，做得不错。"
- "论文下载了，但 README 里核心技术原理部分太简略，把论文摘要的内容补上。"
- "好像少了文件内容，把 PDF 里的核心架构描述加上。"
- "第三步直接跳过了？从第二步到第四步中间缺了一块。"
- "可以，完成了。arXiv 论文找到了，README 也生成了，内容都到位。"

❌ 不好的例子：
- "经评估，任务未完成，建议补充第二步和第三步之间的内容。"（太正式）
- "任务整体完成度较高，但在某些细节上还有完善空间。"（太客气、太模糊）
- "步骤不完整，逻辑不连贯，结果不到位。"（泛泛而谈，没说具体什么）
- "README 文件已生成。"（只说文件名，没总结内容）

## 返回格式（必须严格遵守）
判断：completed/incomplete
反馈：(先总结文件内容，再评价任务完成情况，1-3 句话)
""",
            max_verification_rounds=5,
        )

        if not results:
            print("  ❌ 未返回结果")
            return False

        print("  ✅ 任务执行完成")
    except Exception as e:
        print(f"  ❌ 任务执行失败：{e}")
        import traceback

        traceback.print_exc()
        return False

    # 4. 显示结果
    print("\n[4/4] 显示结果...")
    print()
    for i, trajectory in enumerate(results):
        format_trajectory_result(trajectory)
        print()

    print("=" * 60)
    print("冒烟测试完成!")
    print("=" * 60)

    return True


def main():
    """主函数"""
    # 支持从命令行传入任务 JSON
    if len(sys.argv) > 1:
        try:
            task_json = " ".join(sys.argv[1:])
            task = json.loads(task_json)
            print(f"使用命令行传入的任务：{task.get('task_id', 'N/A')}")
        except json.JSONDecodeError as e:
            print(f"❌ 无效的 JSON: {e}")
            print(
                '用法：python test_smoke.py \'{"task_id": "...", "question": "..."}\''
            )
            return 1
    else:
        task = None

    success = run_smoke_test()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
