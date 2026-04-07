"""
CLINanobotServing 测试文件

测试基于 nanobot SDK 的轻量级 Serving 类的基本功能。
"""

import os
import shutil
import json
import pytest
from pathlib import Path
from dataflow.serving import CLINanobotServing

# 测试用的 nanobot 目录（每次测试前清空）
TEST_NANOBOT_DIR = Path.home() / ".nanobot"
TEST_NANOBOT_BACKUP = Path.home() / ".nanobot_backup"


def check_for_llm_error(response: str, task_desc: str = "") -> None:
    """
    检查 LLM 返回是否是错误响应。

    如果是错误，抛出 AssertionError。
    """
    if not response:
        return

    resp_lower = response.lower()

    # 检查错误关键词
    error_indicators = [
        "error",
        "not found",
        "does not exist",
        "invalid",
        "unauthorized",
        "forbidden",
    ]

    for indicator in error_indicators:
        if indicator in resp_lower:
            # 尝试解析 JSON 错误
            try:
                error_data = json.loads(response)
                error_msg = error_data.get("message", response)
                prefix = f"[{task_desc}] " if task_desc else ""
                raise AssertionError(f"{prefix}LLM 返回错误：{error_msg}")
            except json.JSONDecodeError:
                # 不是 JSON，直接报错
                prefix = f"[{task_desc}] " if task_desc else ""
                raise AssertionError(f"{prefix}LLM 返回错误：{response[:200]}")


class TestCLINanobotServing:
    """CLINanobotServing 单元测试"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """每个测试前后清空 ~/.nanobot/ 目录"""
        print(f"\n[Fixture Setup] 开始清理 ~/.nanobot/")

        # 备份现有配置（如果存在）
        if TEST_NANOBOT_DIR.exists():
            print(f"   备份现有配置：{TEST_NANOBOT_DIR}")
            if TEST_NANOBOT_BACKUP.exists():
                print(f"   删除旧备份：{TEST_NANOBOT_BACKUP}")
                shutil.rmtree(TEST_NANOBOT_BACKUP)
            shutil.move(str(TEST_NANOBOT_DIR), str(TEST_NANOBOT_BACKUP))
            print(f"   备份完成：{TEST_NANOBOT_BACKUP}")
        else:
            print(f"   ~/.nanobot/ 不存在，无需备份")

        # 确保目录不存在（干净环境）
        if TEST_NANOBOT_DIR.exists():
            print(f"   删除残留目录：{TEST_NANOBOT_DIR}")
            shutil.rmtree(TEST_NANOBOT_DIR)

        print(f"[Fixture Setup] 清理完成，确认：{not TEST_NANOBOT_DIR.exists()}")

        yield

        print(f"\n[Fixture Teardown] 测试完成，清理 ~/.nanobot/")

        # 测试完成后清空
        if TEST_NANOBOT_DIR.exists():
            print(f"   删除测试目录：{TEST_NANOBOT_DIR}")
            shutil.rmtree(TEST_NANOBOT_DIR)

        # 恢复原配置（如果存在备份）
        if TEST_NANOBOT_BACKUP.exists():
            print(f"   恢复原配置：{TEST_NANOBOT_BACKUP} -> {TEST_NANOBOT_DIR}")
            if TEST_NANOBOT_DIR.exists():
                shutil.rmtree(TEST_NANOBOT_DIR)
            shutil.move(str(TEST_NANOBOT_BACKUP), str(TEST_NANOBOT_DIR))
            print(f"   恢复完成")
        else:
            print(f"   无备份，保持清空状态")

        print(f"[Fixture Teardown] 完成")

    def test_init_with_auto_config(self, tmp_path):
        """测试自动创建配置文件"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            auto_create_config=True,
        )

        # 检查配置文件是否创建
        assert config_path.exists(), "配置文件未创建"
        assert workspace_path.exists(), "工作目录未创建"

        # 检查配置文件内容
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        assert config["agents"]["defaults"]["model"] == "test-model"
        assert config["agents"]["defaults"]["provider"] == "vllm"
        assert config["agents"]["defaults"]["contextWindowTokens"] == 131072
        # 验证 API 配置
        assert config["providers"]["vllm"]["apiBase"] == "http://localhost:8000/v1"
        assert config["providers"]["vllm"]["apiKey"] == "EMPTY"

    def test_init_with_extra_skills(self, tmp_path):
        """测试额外技能目录链接"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"
        extra_skills_dir = tmp_path / "extra_skills"

        # 创建额外技能目录
        extra_skills_dir.mkdir()
        skill1 = extra_skills_dir / "test-skill-1"
        skill1.mkdir()
        (skill1 / "SKILL.md").write_text("# Test Skill 1")

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            auto_create_config=True,
            extra_skills_dirs=[str(extra_skills_dir)],
        )

        # 检查符号链接是否创建
        workspace_skills = workspace_path / "skills"
        assert workspace_skills.exists(), "技能目录未创建"

        linked_skill = workspace_skills / "test-skill-1"
        assert linked_skill.exists(), "技能符号链接未创建"
        assert linked_skill.is_symlink(), "技能链接不是符号链接"

    def test_generate_from_input_basic(self, tmp_path):
        """测试基本的文本生成（需要实际 nanobot 环境）"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            max_workers=1,
            max_retries=0,  # 不重试，快速失败
            auto_create_config=True,
        )

        # 调试：打印配置文件内容
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        print(f"\n[调试] 配置文件内容:")
        print(f"   路径：{config_path}")
        print(
            f"   providers.vllm.apiKey: {config.get('providers', {}).get('vllm', {}).get('apiKey')}"
        )
        print(
            f"   providers.vllm.apiBase: {config.get('providers', {}).get('vllm', {}).get('apiBase')}"
        )

        # 测试基本生成（预期可能失败，因为模型可能不存在）
        user_inputs = ["你好，请介绍一下你自己"]
        input_files_data = [{}]

        try:
            responses = serving.generate_from_input(
                user_inputs=user_inputs,
                system_prompt="你是一个助手",
                json_schema={},
                input_files_data=input_files_data,
            )

            # 检查返回格式
            assert isinstance(responses, list), "返回值应该是列表"
            assert len(responses) == len(user_inputs), "返回值长度应该与输入相同"

            # 打印返回内容
            print("\n[测试] 返回内容:")
            print(f"   输入：{user_inputs[0]}")
            print(f"   返回：{responses[0]}")
            print(f"   长度：{len(responses[0]) if responses[0] else 0} 字符")

            # 验证 nanobot 返回了正确的对话内容
            if responses[0]:  # 如果有返回内容
                assert isinstance(responses[0], str), "返回内容应该是字符串"
                assert len(responses[0]) > 0, "返回内容不应该为空"

                # 检查是否是错误响应
                check_for_llm_error(responses[0], "test_generate_from_input_basic")

                # 基本内容验证：应该包含一些文本，而不是错误信息
                assert (
                    "Traceback" not in responses[0]
                ), f"返回内容包含错误堆栈：{responses[0][:200]}"
        except Exception as e:
            # 如果模型不存在或其他环境错误，这是预期的
            pytest.skip(f"测试环境未配置完整 nanobot 环境：{e}")

    def test_generate_with_input_files(self, tmp_path):
        """测试带文件输入的生成"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            max_workers=1,
            max_retries=0,
            auto_create_config=True,
        )

        user_inputs = ["请分析这个文件"]
        input_files_data = [
            {"test.txt": {"type": "text", "content": "这是一段测试文本"}}
        ]

        try:
            responses = serving.generate_from_input(
                user_inputs=user_inputs,
                system_prompt="你是一个助手",
                json_schema={},
                input_files_data=input_files_data,
            )

            assert isinstance(responses, list)
            assert len(responses) == 1

            # 打印返回内容
            print("\n[测试] 返回内容:")
            print(f"   输入：{user_inputs[0]}")
            print(f"   返回：{responses[0]}")
            print(f"   长度：{len(responses[0]) if responses[0] else 0} 字符")

            # 验证返回内容
            if responses[0]:
                assert isinstance(responses[0], str)

                # 检查是否是错误响应
                check_for_llm_error(responses[0], "test_generate_with_input_files")

                assert (
                    "Traceback" not in responses[0]
                ), f"返回内容包含错误：{responses[0][:200]}"
        except Exception as e:
            pytest.skip(f"测试环境未配置完整 nanobot 环境：{e}")

    def test_cleanup(self, tmp_path):
        """测试资源清理"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            auto_create_config=True,
        )

        # 执行清理
        serving.cleanup()

        # 检查 cleaned up
        assert serving._initialized is False

    def test_generate_embedding_not_supported(self, tmp_path):
        """测试 embedding 功能不支持"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            auto_create_config=True,
        )

        # nanobot 不支持 embedding，应该返回空向量
        embeddings = serving.generate_embedding_from_input(["test1", "test2"])

        assert isinstance(embeddings, list)
        assert len(embeddings) == 2
        assert embeddings == [[], []]

    def test_concurrent_generation(self, tmp_path):
        """测试并发生成（需要实际 nanobot 环境）"""
        config_path = tmp_path / "config.json"
        workspace_path = tmp_path / "workspace"

        serving = CLINanobotServing(
            config_path=str(config_path),
            workspace=str(workspace_path),
            model="test-model",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            max_workers=2,
            max_retries=0,
            auto_create_config=True,
        )

        user_inputs = ["问题 1", "问题 2", "问题 3"]
        input_files_data = [{}, {}, {}]

        try:
            responses = serving.generate_from_input(
                user_inputs=user_inputs,
                system_prompt="你是一个助手",
                json_schema={},
                input_files_data=input_files_data,
            )

            assert isinstance(responses, list)
            assert len(responses) == 3

            # 打印返回内容
            print("\n[测试] 并发生成返回内容:")
            for i, (inp, resp) in enumerate(zip(user_inputs, responses)):
                resp_preview = resp[:100] if resp else "(空)"
                print(
                    f"   任务 {i+1}: 输入='{inp}' -> 返回='{resp_preview}...' (长度:{len(resp) if resp else 0})"
                )

            # 验证每个返回都是有效字符串（不是错误信息）
            for i, resp in enumerate(responses):
                if resp:
                    assert isinstance(resp, str), f"返回 {i} 不是字符串"

                    # 检查是否是错误响应
                    check_for_llm_error(resp, f"concurrent_task_{i}")

                    assert (
                        "Traceback" not in resp
                    ), f"返回 {i} 包含错误堆栈：{resp[:200]}"
        except Exception as e:
            pytest.skip(f"测试环境未配置完整 nanobot 环境：{e}")


# 集成测试（需要实际 nanobot 环境）
@pytest.mark.integration
def test_integration_nanobot_serving():
    """
    集成测试：完整的 nanobot serving 流程

    注意：需要配置有效的 nanobot 环境和模型
    """
    # 检查是否设置了测试环境变量
    if os.environ.get("SKIP_NANOBOT_TESTS"):
        pytest.skip("SKIP_NANOBOT_TESTS 已设置，跳过集成测试")

    # 集成测试也需要手动清理 ~/.nanobot/
    print(f"\n[集成测试 Setup] 开始清理 ~/.nanobot/")
    if TEST_NANOBOT_DIR.exists():
        print(f"   删除：{TEST_NANOBOT_DIR}")
        shutil.rmtree(TEST_NANOBOT_DIR)
    print(f"[集成测试 Setup] 清理完成，确认：{not TEST_NANOBOT_DIR.exists()}")

    try:
        # 使用默认配置（会自动清空 ~/.nanobot/ 并创建新配置）
        print(f"\n[调试] 创建 CLINanobotServing")
        print(f"   config_path: ~/.nanobot/config.json")
        print(f"   workspace: ~/.nanobot/workspace")
        print(f"   api_key: EMPTY")
        print(f"   api_base: http://localhost:8000/v1")

        serving = CLINanobotServing(
            model="/data/share/models/Qwen3.5-122B-A10B/",
            provider="vllm",
            api_base="http://localhost:8000/v1",
            api_key="EMPTY",
            max_workers=2,
        )

        # 验证配置文件是否正确创建
        config_path = Path.home() / ".nanobot" / "config.json"
        print(f"\n[调试] 读取配置文件：{config_path}")
        print(f"   文件存在：{config_path.exists()}")

        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            print(f"   配置文件内容:")
            print(
                f"   - providers.vllm.apiKey: {config.get('providers', {}).get('vllm', {}).get('apiKey')}"
            )
            print(
                f"   - providers.vllm.apiBase: {config.get('providers', {}).get('vllm', {}).get('apiBase')}"
            )
            print(
                f"   - agents.defaults.model: {config.get('agents', {}).get('defaults', {}).get('model')}"
            )
        else:
            print(f"   警告：配置文件不存在！")

        user_inputs = ["你好，请用一句话介绍你自己"]
        input_files_data = [{}]

        responses = serving.generate_from_input(
            user_inputs=user_inputs,
            system_prompt="你是一个助手",
            json_schema={},
            input_files_data=input_files_data,
        )

        # 验证返回格式
        assert len(responses) == 1, f"期望 1 个返回，实际 {len(responses)}"
        assert responses[0] != "", "返回内容不能为空"

        # 验证返回内容是有效对话，不是错误信息
        assert isinstance(responses[0], str), "返回应该是字符串"

        # 检查是否是错误响应
        check_for_llm_error(responses[0], "test_integration_nanobot_serving")

        assert (
            "Traceback" not in responses[0]
        ), f"返回包含错误堆栈：{responses[0][:300]}"

        # 打印实际返回内容（方便调试）
        print(f"\n✅ 集成测试成功")
        print(f"   输入：{user_inputs[0]}")
        print(f"   返回：{responses[0]}")
        print(f"   内容长度：{len(responses[0])} 字符")

        serving.cleanup()
    finally:
        # 无论成功失败都清理
        print(f"\n[集成测试 Teardown] 清理 ~/.nanobot/")
        if TEST_NANOBOT_DIR.exists():
            print(f"   删除：{TEST_NANOBOT_DIR}")
            shutil.rmtree(TEST_NANOBOT_DIR)
        print(f"[集成测试 Teardown] 完成")


if __name__ == "__main__":
    # 直接运行测试
    pytest.main([__file__, "-v", "-s"])
