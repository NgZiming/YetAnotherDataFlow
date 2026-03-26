"""
Pipeline 模块 🔄

提供数据流管道的核心基类和实现，支持：
- 基础管道执行 (PipelineABC)
- 分片并行执行 (PartitionPipelineParallelRun)
- 管道可视化 (draw_graph) 📊
- 断点续传支持 💾
"""

# ==================== 标准库 ====================
import atexit
import copy
import os
import socket
from abc import ABC, abstractmethod
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ==================== 第三方库 ====================
import pandas as pd

# ==================== 项目本地 ====================
import webbrowser

from dataflow.core import LLM_SERVING_CLASSES
from dataflow.core.operator import OperatorABC
from dataflow.logger import get_logger
from dataflow.pipeline.nodes import KeyNode, OperatorNode
from dataflow.utils.storage import (
    CacheStorage,
    ProgressInfo,
    PartitionOrBatchProgress,
)
from dataflow.utils.storage import PartitionableStorage as DataFlowStorage
from dataflow.wrapper.auto_op import AutoOP, OPRuntime


@dataclass(frozen=True)
class Workload:
    """工作负载单元 📦

    表示一个具体的执行任务，由分片编号和步骤编号组成。

    Attributes:
        partition: 分片编号（从 0 开始）
        step: 算子步骤索引（从 0 开始）

    Example:
        Workload(partition=0, step=1)  # 第 1 个分片的第 2 个步骤
    """

    partition: int
    step: int


class PipelineABC(ABC):
    def __init__(self, cache_storage: CacheStorage):
        """初始化管道基类。

        Args:
            cache_storage: 用于持久化执行进度的存储对象
        """
        self.op_runtimes: list[OPRuntime] = []  # 算子运行时列表 🏃
        self.compiled = False  # 是否已编译
        self.accumulated_keys: list[list] = []  # 累积的字段名列表 🔑

        self.logger = get_logger()
        self.active_llm_serving = None  # 当前活跃的 LLM Serving 🤖
        self.op_nodes_list: list[OperatorNode] = []  # 算子节点列表
        self.llm_serving_list: list = []  # LLM Serving 对象列表
        self.llm_serving_counter = Counter()  # LLM Serving 引用计数
        self.cache_storage = cache_storage  # 进度存储 💾

    @abstractmethod
    def forward(self):
        """管道执行入口 🚀

        子类必须实现此方法，定义具体的执行逻辑。
        """
        pass

    def compile(self):
        """编译管道 🔧

        执行以下操作：
        1. 标记管道为已编译状态
        2. 将所有 Operator 包装为 AutoOP（自动注入运行时信息）
        3. 调用 forward() 触发包装回调，收集 OPRuntime
        4. 构建算子节点图，验证字段名完整性
        """
        self.compiled = True
        # 包装所有 Operator 为 AutoOP
        for k, v in vars(self).items():
            if isinstance(v, OperatorABC):
                setattr(self, k, AutoOP(v, k, self))

        # 触发包装回调，收集 OPRuntime
        self.forward()

        # 替换 forward 为编译后的版本
        self.forward = self._compiled_forward

        self.logger.info(
            f"🔧 编译管道 | 验证 {len(self.op_runtimes)} 个算子的字段名完整性..."
        )
        self._build_operator_nodes_graph()

    def _build_operator_nodes_graph(self):
        """构建算子节点图 📊

        从 OPRuntime 中提取算子对象和 Storage，构建 OperatorNode 列表。
        同时提取 LLM Serving 对象用于后续资源管理。
        """
        for op_runtime in self.op_runtimes:
            llm_serving_obj, storage_obj = None, None

            # 从算子对象中提取 LLM Serving
            for _, v in vars(op_runtime.op).items():
                if isinstance(v, LLM_SERVING_CLASSES):
                    llm_serving_obj = v

            # 从 kwargs 中提取 Storage
            storage_obj = op_runtime.kwargs.pop("storage", None)

            assert isinstance(
                storage_obj, DataFlowStorage
            ), f"Storage 必须是 DataFlowStorage 对象，但得到 {type(storage_obj)} in {op_runtime}'s `run` function with key `storage`."

            # 创建算子节点
            op_node = OperatorNode(
                op_obj=op_runtime.op,
                op_name=op_runtime.op_name,
                storage=storage_obj,
                llm_serving=llm_serving_obj,
                **op_runtime.kwargs,
            )

            self.op_nodes_list.append(op_node)
            self.llm_serving_list.append(llm_serving_obj)
            if llm_serving_obj is not None:
                self.llm_serving_counter[llm_serving_obj] += 1

        self.logger.debug(
            f"📊 构建算子节点图 | {len(self.op_nodes_list)} 个节点, {len([l for l in self.llm_serving_list if l])} 个 LLM Serving"
        )

        # get keys in the first storage:
        first_op = self.op_nodes_list[0] if self.op_nodes_list else None
        if first_op and isinstance(first_op.storage, DataFlowStorage):
            iter_storage_keys = first_op.storage.get_keys()
        else:
            iter_storage_keys = []

        # print("start keys", iter_storage_keys)

        # all keys in the first storage will be the initial keys for validation
        self.accumulated_keys.append(copy.deepcopy(iter_storage_keys))

        error_msg = []
        # build graph of all operators and keys from all states
        for op_node in self.op_nodes_list:
            # check if accumulated_keys have the input keys of this operator
            # print(op_node, op_node.input_keys, op_node.output_keys)
            for input_key in op_node.input_keys:
                if input_key not in self.accumulated_keys[-1]:
                    error_msg.append(
                        {
                            "input_key": input_key,
                            "op_name": op_node.op_name,
                            "class_name": op_node.op_obj.__class__.__name__,
                            "key_para_name": op_node.input_key_nodes[
                                input_key
                            ].key_para_name,
                        }
                    )

            # add output keys to accumulated keys
            for output_key in op_node.output_keys:
                if output_key not in iter_storage_keys:
                    iter_storage_keys.append(output_key)
            self.accumulated_keys.append(copy.deepcopy(iter_storage_keys))
        if len(error_msg) != 0:
            # final_error_str = "KeyError in following Operators during pipeline.compile():"
            details = "\n".join(
                f"- Input key '{e['input_key']}' in `{e['op_name']}` "
                f"(class <{e['class_name']}>) does not match any output keys "
                f"from previous operators or dataset keys. "
                f"Check parameter '{e['key_para_name']}' in the `{e['op_name']}.run()`."
                for e in error_msg
            )
            msg = f"Key Matching Error in following Operators during pipeline.compile():\n{details}"
            self.logger.warning(msg)
            raise KeyError(msg)

        self.final_keys = copy.deepcopy(iter_storage_keys)

        for i, keys in enumerate(self.accumulated_keys):
            # print(i, keys)
            pass
        self.logger.debug(
            f"Accumulated keys after building graph: {self.accumulated_keys}"
        )

        self.input_dataset_node = OperatorNode(
            None,
            "DATASET-INPUT",
            None,
            None,
        )
        self.input_dataset_node.init_output_keys_nodes(self.accumulated_keys[0])
        self.op_nodes_list.insert(0, self.input_dataset_node)

        self.output_dataset_node = OperatorNode(
            None,
            "DATASET-OUTPUT",
            None,
            None,
        )
        self.output_dataset_node.init_input_keys_nodes(self.final_keys)
        self.op_nodes_list.append(self.output_dataset_node)

        # set a default dict for all keys
        self.last_modified_index_of_keys: dict[list] = {}
        for key in self.final_keys:
            self.last_modified_index_of_keys[key] = []
        # print(self.last_modified_index_of_keys)

        # now the first op node is THEDATASET op
        for idx, i_op in enumerate(self.op_nodes_list):
            # check for input keys
            for input_key in i_op.input_keys:
                current_keynode: KeyNode = i_op.input_key_nodes[input_key]
                current_keynode.set_index(idx)

                if len(self.last_modified_index_of_keys[input_key]) > 0:
                    last_modified_idx = self.last_modified_index_of_keys[input_key][-1]
                    last_modified_keynode: KeyNode = self.op_nodes_list[
                        last_modified_idx
                    ].output_keys_nodes[input_key]
                    # double side ptr for each nodes
                    last_modified_keynode.ptr.append(current_keynode)
                    current_keynode.ptr.append(last_modified_keynode)
            # check for output keys
            for output_key in i_op.output_keys:
                current_keynode: KeyNode = i_op.output_keys_nodes[output_key]
                current_keynode.set_index(idx)
                self.last_modified_index_of_keys[output_key].append(idx)

        for key, value in self.last_modified_index_of_keys.items():
            # print(key, value)
            pass

        for op in self.op_nodes_list:
            # print(op)
            self.logger.debug(f"Operator Node: {op}")
            pass

    # deprecated, use `draw_graph` instead, archived for compatibility
    def _draw_graph_for_operators(self):
        raise DeprecationWarning(
            "The `_draw_graph_for_operators` method is deprecated. "
            "Please use `draw_graph` method instead for better visualization."
        )

    def draw_graph(self, port=0, hide_no_changed_keys=True):
        # 检查是否已编译
        if not self.compiled:
            self.logger.error("❌ 管道未编译 | 请先调用 `compile()`")
            raise RuntimeError("管道未编译 | 请先调用 `compile()`")
        # import check if pyvis is installed
        try:
            from pyvis.network import Network
        except ImportError:
            raise ImportError(
                "Please install pyvis to draw graph of current pipeline. Please run `pip install pyvis`."
            )

        def _get_op_node_str(node, step=None):
            op_class_name = (
                node.op_obj.__class__.__name__
                if node.op_obj.__class__.__name__ != "NoneType"
                else "Storage/No-Op"
            )
            if step is not None:
                return f"{node.op_name}\n<{op_class_name}>\n(step={step})\n"
            else:
                return f"{node.op_name}\n<{op_class_name}>\n"

        def _get_op_node_title(node):
            input_keys_string = ""
            op_class_name = (
                node.op_obj.__class__.__name__
                if node.op_obj.__class__.__name__ != "NoneType"
                else "Storage/No-Op"
            )
            if op_class_name == "Storage/No-Op":
                for i_key_node in node.input_key_nodes.values():
                    input_keys_string += f"  {i_key_node.key}\n"
                output_keys_string = ""
                for o_key_node in node.output_keys_nodes.values():
                    output_keys_string += f"  {o_key_node.key}\n"
            else:
                for i_key_node in node.input_key_nodes.values():
                    input_keys_string += (
                        f"  {i_key_node.key_para_name}={i_key_node.key}\n"
                    )
                output_keys_string = ""
                for o_key_node in node.output_keys_nodes.values():
                    output_keys_string += (
                        f"  {o_key_node.key_para_name}={o_key_node.key}\n"
                    )

            if input_keys_string == "":
                input_keys_string = "  None\n"
            if output_keys_string == "":
                output_keys_string = "  None\n"
            return (
                f"Attrbute: {node.op_name}\n"
                f"Class: {op_class_name}\n"
                f"------\n"
                f"Input:\n {input_keys_string}"
                f"------\n"
                f"Output:\n {output_keys_string}"
            )

        def _hex_to_rgb(hex_color):
            hex_color = hex_color.lstrip("#")
            return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))

        def _rgb_to_hex(rgb):
            return "#{:02x}{:02x}{:02x}".format(*rgb)

        def _lerp_color(c1, c2, t):
            return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

        def _step_to_color(step, total_steps):
            """
            冷淡风渐变: 灰蓝 → 灰紫 → 冷蓝
            """
            # start = _hex_to_rgb("#bfdefc")  # 浅钢蓝
            # mid   = _hex_to_rgb("#aaa1d3")  # 灰紫
            # end   = _hex_to_rgb("#888794")  # 冷蓝灰
            # start = _hex_to_rgb("#FCF1D0")  # 浅钢蓝
            # mid   = _hex_to_rgb("#DBFFDD")  # 灰紫
            # end   = _hex_to_rgb("#C8E0F9")  # 冷蓝灰
            start = _hex_to_rgb("#D5B4EC")  # 浅钢蓝
            mid = _hex_to_rgb("#879DF8")  # 灰紫
            end = _hex_to_rgb("#81CDF9")  # 冷蓝灰

            if total_steps <= 1:
                return _rgb_to_hex(start)

            mid_point = (total_steps - 1) / 2
            if step <= mid_point:
                t = step / mid_point
                rgb = _lerp_color(start, mid, t)
            else:
                t = (step - mid_point) / mid_point
                rgb = _lerp_color(mid, end, t)

            return _rgb_to_hex(rgb)

        # def _step_to_color(step, total_steps):
        #     # 红色 (255, 0, 0) → 蓝色 (0, 0, 255)
        #     r_start, g_start, b_start = (255, 0, 0)
        #     r_end, g_end, b_end = (0, 0, 255)
        #     t = step / max(total_steps - 1, 1)  # 归一化到 [0, 1]
        #     r = int(r_start + (r_end - r_start) * t)
        #     g = int(g_start + (g_end - g_start) * t)
        #     b = int(b_start + (b_end - b_start) * t)
        #     return f"#{r:02x}{g:02x}{b:02x}"

        # def _step_to_color(step, total_steps):
        #     """
        #     从红 → 紫 → 蓝的平滑渐变
        #     """
        #     if total_steps <= 1:
        #         return "#ff0000"  # 只有一个节点时直接红色

        #     mid_point = (total_steps - 1) / 2
        #     if step <= mid_point:
        #         # 红(0°) → 紫(300°)
        #         h_start, h_end = 0 / 360, 300 / 360
        #         t = step / mid_point
        #     else:
        #         # 紫(300°) → 蓝(240°)
        #         h_start, h_end = 300 / 360, 240 / 360
        #         t = (step - mid_point) / mid_point

        #     # 饱和度和亮度固定高值
        #     s, l = 1.0, 0.5
        #     h = h_start + (h_end - h_start) * t
        #     r, g, b = colorsys.hls_to_rgb(h, l, s)

        #     return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

        # 生成 PyVis 图
        net = Network(height="800px", width="100%", directed=True)
        net.force_atlas_2based()
        net.toggle_physics(True)
        net.set_options(
            """
        {
            "physics": {
                "forceAtlas2Based": {
                    "springLength": 300,
                    "springConstant": 0.01
                },
                "minVelocity": 0.75,
                "solver": "forceAtlas2Based"
            }
        }
        """
        )

        for idx, op_node in enumerate(self.op_nodes_list):
            node_color = _step_to_color(idx, len(self.op_nodes_list))
            net.add_node(
                n_id=id(op_node),
                label=_get_op_node_str(op_node, step=idx),
                title=_get_op_node_title(op_node),
                color=node_color,
                shape="box",
            )

        for op_node in self.op_nodes_list:
            for output_key_nodes in op_node.output_keys_nodes.values():
                for ptr_key_node in output_key_nodes.ptr:
                    target_node = self.op_nodes_list[ptr_key_node.index]
                    if (
                        hide_no_changed_keys
                        and op_node == self.op_nodes_list[0]
                        and target_node == self.op_nodes_list[-1]
                    ):
                        # hide the keys that are not changed from input dataset to first operator
                        continue

                    net.add_edge(
                        source=id(op_node),
                        to=id(target_node),
                        label=ptr_key_node.key,
                        color="gray",
                    )

        # Timestamped filename to avoid overwriting
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        os.makedirs(".pyvis", exist_ok=True)
        output_html = os.path.abspath(
            os.path.join(".pyvis", f"operators_graph_{ts}.html")
        )
        net.save_graph(output_html)

        # Automatically delete the file on exit (whether normal exit or Ctrl-C)
        def _cleanup():
            try:
                if os.path.exists(output_html):
                    os.remove(output_html)
                    self.logger.debug(f"🧹 清理临时文件：{output_html}")
            except Exception as e:
                print(f"Failed to clean up file: {e}")

        atexit.register(_cleanup)

        # Select port
        if port == 0:
            sock = socket.socket()
            sock.bind(("", 0))
            port = sock.getsockname()[1]
            sock.close()

        # Start HTTP service (main thread blocking)
        class SilentHandler(SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

        # Change to the directory where the file is located so the static service can find it
        orig_cwd = os.getcwd()
        try:
            serve_dir = os.path.dirname(output_html) or "."
            os.chdir(serve_dir)
            url = f"http://localhost:{port}/{os.path.basename(output_html)}"
            self.logger.info(f"✅ 生成图谱 | 访问地址：{url}")

            with HTTPServer(("0.0.0.0", port), SilentHandler) as httpd:
                print(
                    f"HTTP service started, listening on port {port} (Ctrl-C to exit, HTML file will be deleted on exit)"
                )
                try:
                    webbrowser.open(url)
                    httpd.serve_forever()

                except KeyboardInterrupt:
                    print(
                        "\n❌ Interrupt signal received, exiting and cleaning up file..."
                    )
        except Exception as e:
            self.logger.error(f"❌ HTTP 服务启动失败：{e}")
            self.logger.error(f"Failed to start HTTP service: {e}")
        finally:
            os.chdir(orig_cwd)

    def _compiled_forward(self, resume_from_last: bool = True):
        """编译后的执行逻辑 🔄

        Args:
            resume_from_last: 是否从上次中断处继续（断点续传）

        执行流程：
        1. 读取进度信息（如有）
        2. 确定恢复的 step 和 batch
        3. 按顺序执行每个算子节点
        4. 管理 LLM Serving 的占用和释放
        """
        if resume_from_last:
            progress = self.cache_storage.get_progress()
            if not progress or progress.get("total_shards", 0) == 0:
                progress = ProgressInfo(
                    shard_type="partition",
                    total_steps=len(self.op_nodes_list) - 2,
                    total_shards=1,
                    partitions=[
                        PartitionOrBatchProgress(
                            id=0,
                            completed_steps=[],
                            current_steps=[],
                            steps_rows_nums={},
                        )
                    ],
                    overall_status="running",
                    start_time=None,
                    last_update=None,
                    error_message=None,
                    extra={},
                    pipeline_class="PipelineABC",
                    op_list=[
                        node.op_name
                        for node in self.op_nodes_list
                        if node.op_obj is not None
                    ],
                )
            assert progress["pipeline_class"] == "PipelineABC"
            assert progress["total_steps"] == len(self.op_nodes_list) - 2
            assert progress["op_list"] == [
                node.op_name for node in self.op_nodes_list if node.op_obj is not None
            ]
            assert len(progress["partitions"]) == 1 and progress["total_shards"] == 1
            resume_step = progress["total_steps"]
            x = progress["partitions"][0]
            for i in range(progress["total_steps"]):
                if i not in x["completed_steps"]:
                    resume_step = i
            resume_step += 1
            resume_batch = 0
            bs = 0
            progress["partitions"][0]["current_steps"] = []
            self.cache_storage.record_progress(progress)
            assert resume_batch == 0 and bs == 0
        else:
            resume_step = 0

        # for loop for each op and its `storage` status
        for idx, op_node in enumerate(self.op_nodes_list):
            # resume from a expected step
            if idx - 1 < resume_step:  # minus one since INPUT-DATA Node
                continue
            if resume_from_last:
                progress["partitions"][0]["current_steps"] = [idx]
                self.cache_storage.record_progress(progress)

            self.logger.debug(
                f"Ready to run {op_node}, with serving={op_node.llm_serving}, active_llm_serving={self.active_llm_serving}"
            )
            if op_node.llm_serving is not None:
                if (
                    self.active_llm_serving
                    and self.active_llm_serving is not op_node.llm_serving
                ):
                    self.logger.debug(
                        f"Detected active LLM Serving {self.active_llm_serving}, new serving {op_node.llm_serving}, cleaning up..."
                    )
                    self.active_llm_serving.cleanup()
                self.active_llm_serving = op_node.llm_serving

            if op_node.op_obj != None:
                op_node.op_obj.run(storage=op_node.storage, **op_node.kwargs)

            if resume_from_last:
                progress["partitions"][0]["current_steps"] = []
                progress["partitions"][0]["completed_steps"].append(idx)
                self.cache_storage.record_progress(progress)

            if op_node.llm_serving is not None:
                self.llm_serving_counter[self.active_llm_serving] -= 1
                if self.llm_serving_counter[self.active_llm_serving] == 0:
                    self.logger.debug(
                        f"Detected LLM Serving {self.active_llm_serving} ref reduced to 0, cleaning up..."
                    )
                    self.active_llm_serving.cleanup()
                    self.active_llm_serving = None


class PartitionPipelineParallelRun(PipelineABC):
    """
    分片并行执行管道 - 支持多分片、多步骤的并发执行。

    核心特性：
    1. **分片并行**：将数据集划分为多个分片，不同分片可并行处理
    2. **步骤依赖**：每个分片内部保持步骤顺序依赖（step N 依赖 step N-1）
    3. **断点续传**：通过文件存在性检查自动跳过已完成的分片/步骤
    4. **并发控制**：通过 max_parallelism 限制同时执行的 workload 数量

    设计思路：
    - 每个 (partition, step) 组合称为一个 Workload
    - 同一 partition 内，step 之间存在依赖关系（前一步完成才能执行下一步）
    - 不同 partition 之间完全独立，可并行执行
    - 使用 ThreadPoolExecutor 实现并发，通过依赖检查确保执行顺序

    进度记录（通过 cache_storage）：
        - part: 当前已完成的分片编号（从 0 开始）
        - step: 当前分片内已完成的 operator 步骤索引
        - part_size: 每个分片的大小（记录数）

    注意：
        进度记录仅用于追踪"理论上应完成的进度"，实际执行通过文件检查判断。
        首次运行时，进度记录会被初始化为"已完成"状态，实际执行时通过文件存在性跳过。
    """

    def __init__(self, cache_storage: CacheStorage, partitions: int = 1):
        """初始化并行分片管道 🔄

        Args:
            cache_storage: 用于持久化执行进度的存储对象 💾
            partitions: 分片数量（默认 1，即串行执行）
        """
        super().__init__(cache_storage)
        self._partitions = partitions

    def compile(self):
        """编译管道并执行数据分片 🔧

        1. 如果存在 storage，先执行 split_input 将数据分片
        2. 调用父类 compile 构建算子图
        """
        if hasattr(self, "storage") and isinstance(self.storage, DataFlowStorage):
            self.logger.info(f"📦 分割输入数据为 {self._partitions} 个分片...")
            self.storage.split_input(self._partitions)

        super().compile()

    def _is_dependent_on(
        self,
        wl1: Workload,
        wl2: Workload,
        dependencies: dict[Workload, set[Workload]],
        visited: set[Workload],
    ) -> bool:
        """
        检查 wl1 是否依赖 wl2（直接或间接）。

        使用 DFS 递归检查依赖链，防止循环依赖。

        Args:
            wl1: 要检查的 workload
            wl2: 目标 workload
            dependencies: 依赖图，Workload -> set[依赖的 Workload]
            visited: 已访问的 workload 集合（防止循环依赖）

        Returns:
            如果 wl1 依赖 wl2（直接或间接）返回 True

        Example:
            如果 A 依赖 B，B 依赖 C，则：
            - _is_dependent_on(A, B) = True
            - _is_dependent_on(A, C) = True（间接依赖）
            - _is_dependent_on(B, A) = False
        """
        if wl1 in visited:
            return False
        visited.add(wl1)

        for dep in dependencies.get(wl1, set()):
            if dep == wl2:
                return True
            if self._is_dependent_on(dep, wl2, dependencies, visited):
                return True

        return False

    def _simplify_dependencies(
        self, dependencies: dict[Workload, set[Workload]]
    ) -> dict[Workload, set[Workload]]:
        """简化依赖关系，消除传递依赖 🔗

        **目的**：减少依赖检查的复杂度，提高调度效率。

        **示例**：
        ```
        原始依赖：
        - A 依赖 B 和 C
        - B 依赖 C

        简化后：
        - A 只依赖 B（因为执行 B 时会自动确保 C 先完成）
        - B 依赖 C
        ```

        Args:
            dependencies: 原始依赖图，Workload -> set[依赖的 Workload]

        Returns:
            简化后的依赖图
        """
        simplified = {}

        for wl, deps in dependencies.items():
            # 对于每个依赖，检查它是否依赖其他依赖
            redundant = set()
            for dep1 in deps:
                for dep2 in deps:
                    if dep1 != dep2:
                        # 检查 dep1 是否依赖 dep2（直接或间接）
                        # 如果 dep1 依赖 dep2，那么 dep2 是冗余的
                        # 因为执行 dep1 时会自动确保 dep2 先完成
                        if self._is_dependent_on(dep1, dep2, dependencies, set()):
                            redundant.add(dep2)

            # 移除冗余依赖
            simplified[wl] = deps - redundant

        return simplified

    def _check_completed_workloads(
        self,
        completed_workloads: set[Workload],
        dependencies: dict[Workload, set[Workload]],
    ):
        """
        检查并标记已完成的 workload，提升调度效率。

        **执行步骤**：
        1. 遍历所有 (partition, step) 组合
        2. 检查每个 workload 的输出文件是否存在
        3. 将已完成的 workload 加入 `completed_workloads` 集合
        4. 从 `dependencies` 中移除已完成任务的依赖

        **目的**：
        - 恢复运行时，已完成的任务直接跳过，不参与调度循环
        - 减少每次迭代中的文件检查次数
        - 依赖图更干净，后续任务更快进入 ready 状态

        Args:
            completed_workloads: 用于记录已完成 workload 的集合（会被修改）
            dependencies: 依赖图（会被修改，移除已完成任务的依赖）
        """
        self.logger.info("🔍 检查已完成的任务...")
        initial_completed = 0

        for partition in range(self._partitions):
            for idx, node in enumerate(self.op_nodes_list):
                # 跳过没有 operator 的节点（如 DATASET-INPUT/OUTPUT）
                if node.op_obj is None:
                    continue

                wl = Workload(partition, idx)
                storage: DataFlowStorage = copy.copy(node.storage)
                storage.batch_step = partition

                # 检查输出文件是否存在
                file_path = storage.write_file_path()
                if storage.file_exists(file_path):
                    completed_workloads.add(wl)
                    initial_completed += 1
                    self.logger.debug(
                        f"✅ 已存在：{node.op_name} 分片 {partition + 1}/{self._partitions}"
                    )

        # 从所有 workload 的依赖中移除已完成的任务
        for other_wl in dependencies.keys():
            dependencies[other_wl] = dependencies[other_wl] - completed_workloads

        total_workload = self._partitions * (len(self.op_nodes_list) - 2)
        self.logger.info(
            f"🎯 进度检查完成 | 已完成 {initial_completed}/{total_workload} 个任务 "
            f"({100 * initial_completed // total_workload}%)"
        )

    def _build_and_prepare_dependencies(
        self,
        completed_workloads: set[Workload],
    ) -> tuple[dict[Workload, set[Workload]], dict[Workload, set[Workload]]]:
        """
        构建依赖图并进行预处理。

        **执行步骤**：
        1. **构建依赖图**：为每个 (partition, step) 创建依赖关系
        2. **简化依赖**：消除传递依赖，减少依赖检查复杂度
        3. **打印依赖图**：输出 partition=1 的依赖关系（调试用）
        4. **检查已完成**：扫描已完成任务，清理依赖图
        5. **返回结果**：完整依赖图 + 已清理的运行时副本

        **返回值说明**：
        - `simplified_dependencies`: 完整的简化依赖图（保持不变），用于提交 workload 时获取依赖
        - `dependencies_checks`: 运行时依赖检查副本（会被修改），用于判断任务是否可执行

        Args:
            completed_workloads: 用于记录已完成 workload 的集合（会被修改）

        Returns:
            (simplified_dependencies, dependencies_checks):
                - simplified_dependencies: 简化后的依赖图（保持完整）
                - dependencies_checks: 用于运行时依赖检查的副本（已完成任务已移除）
        """
        # 构建完整的依赖图：Workload -> set[依赖的 Workload]
        # 每个 (partition, step) 是一个 Workload，依赖来自 input_key_nodes 的前驱节点
        dependencies: dict[Workload, set[Workload]] = dict()

        # 第一步：识别所有 filter 算子的 step 索引
        filter_steps = set()
        for idx, node in enumerate(self.op_nodes_list):
            if node.op_obj is not None:
                class_name = node.op_obj.__class__.__name__
                if "Filter" in class_name:
                    filter_steps.add(idx)
                    self.logger.info(
                        f"🔍 识别到 Filter 算子：{class_name} (step={idx})"
                    )

        # 第二步：构建基础依赖图
        for partition in range(self._partitions):
            for idx, node in enumerate(self.op_nodes_list):
                wl = Workload(partition, idx)
                dependencies[wl] = set()
                for _, key_node in node.input_key_nodes.items():
                    # 添加依赖：当前 workload 依赖前驱节点的 workload
                    dependencies[wl].add(Workload(partition, key_node.ptr[-1].index))

        # 第三步：为 filter 后续步骤添加依赖
        # 如果某个 step 是 filter，那么它之后的所有 step 都应该依赖这个 filter
        for partition in range(self._partitions):
            for idx, node in enumerate(self.op_nodes_list):
                # 跳过 DATASET-INPUT (step=0) 和 DATASET-OUTPUT (最后一个)
                if node.op_obj is None:
                    continue
                # 如果当前 step 前面有 filter，添加依赖
                for filter_step in filter_steps:
                    if filter_step < idx:
                        filter_wl = Workload(partition, filter_step)
                        if filter_wl not in dependencies[Workload(partition, idx)]:
                            dependencies[Workload(partition, idx)].add(filter_wl)
                            self.logger.debug(
                                f"🔗 添加 Filter 依赖：step={idx} ({node.op_name}) 依赖 filter_step={filter_step}"
                            )

        self.logger.debug(
            f"📋 依赖图构建完成 | {len(dependencies)} 个 workload, "
            f"{len(self.op_nodes_list)} 个 operator 节点"
        )

        # 简化依赖关系，消除传递依赖
        simplified_dependencies = self._simplify_dependencies(dependencies)
        original_dep_count = sum(len(d) for d in dependencies.values())
        simplified_dep_count = sum(len(d) for d in simplified_dependencies.values())
        if simplified_dep_count < original_dep_count:
            self.logger.info(
                f"📉 依赖简化完成 | 原始依赖数：{original_dep_count}, "
                f"简化后依赖数：{simplified_dep_count}, 移除：{original_dep_count - simplified_dep_count}"
            )

        # 打印 partition=1 的完整依赖图（用于调试）
        self.logger.info("📋 依赖图 (partition=1):")
        partition_1_deps = [
            (wl, deps)
            for wl, deps in simplified_dependencies.items()
            if wl.partition == 1 and len(deps) > 0
        ]
        for wl, deps in sorted(partition_1_deps, key=lambda x: x[0].step):
            op_name = self.op_nodes_list[wl.step].op_name
            dep_strs = [f"{self.op_nodes_list[d.step].op_name}" for d in deps]
            self.logger.info(f"  {op_name} (step={wl.step}) → [{', '.join(dep_strs)}]")

        # 创建用于运行时检查的副本
        dependencies_checks = copy.deepcopy(simplified_dependencies)

        # 检查已完成的任务，提升调度效率（修改 dependencies_checks）
        self._check_completed_workloads(completed_workloads, dependencies_checks)

        # 返回简化后的依赖图（保持完整）和已清理的运行时检查副本
        return simplified_dependencies, dependencies_checks

    def _compiled_forward(self, max_parallelism=4):
        """执行入口：初始化进度并启动并发执行 🚀

        Args:
            max_parallelism: 最大并发数（同时执行的 workload 数量）

        执行流程：
        1. 读取上次中断时的进度（如有）
        2. 初始化进度信息（全新运行）
        3. 调用 concurrent_execute_operators 启动并发执行
        """
        # 读取上次中断时的进度
        progress: ProgressInfo = self.cache_storage.get_progress()

        # 初始化：如果是全新运行，则创建新的进度信息
        if not progress or progress.get("total_shards", 0) == 0:
            progress = ProgressInfo(
                shard_type="partition",
                total_steps=len(self.op_nodes_list) - 2,
                total_shards=self._partitions,
                partitions=[
                    PartitionOrBatchProgress(
                        id=i,
                        completed_steps=[],
                        current_steps=[],
                        steps_rows_nums={},
                    )
                    for i in range(self._partitions)
                ],
                overall_status="running",
                start_time=None,
                last_update=None,
                error_message=None,
                extra={},
                pipeline_class="PartitionPipelineParallelRun",
                op_list=[
                    node.op_name
                    for node in self.op_nodes_list
                    if node.op_obj is not None
                ],
            )
        assert progress["pipeline_class"] == "PartitionPipelineParallelRun"
        assert progress["total_steps"] == len(self.op_nodes_list) - 2
        assert progress["op_list"] == [
            node.op_name for node in self.op_nodes_list if node.op_obj is not None
        ]
        assert (
            len(progress["partitions"]) == self._partitions
            and progress["total_shards"] == self._partitions
        )

        for partition in progress["partitions"]:
            partition["current_steps"] = []
        self.cache_storage.record_progress(progress)

        self.logger.info(
            f"▶️ 执行 PartitionPipelineParallelRun | partitions={self._partitions}"
        )

        # 启动并发执行，传入进度对象
        self.concurrent_execute_operators(max_parallelism, progress)

    def execute_workload(
        self,
        wl: Workload,
        node: OperatorNode,
        dependencies: set[Workload],
    ) -> int:
        """
        执行单个 Workload（分片 + 步骤组合）。

        **注意**：此方法在线程池中运行，由 `concurrent_execute_operators` 调用。
        LLM Serving 的占用和释放由调用方统一管理。

        Args:
            wl: Workload 对象，包含 partition 和 step 信息
            node: 要执行的 OperatorNode
            dependencies: 该 workload 的依赖集合（同一 partition 内的前驱步骤）

        执行流程：
        1. 记录执行日志
        2. 分析依赖节点的 step 索引
        3. 配置 storage 的 batch_size 和 batch_step
        4. 检查输出文件是否存在（防御性断点续传检查）
        5. 加载前驱步骤的数据
        6. 执行 operator.run()
        """
        self.logger.info(
            f"▶️ 执行 [{wl.partition + 1}/{self._partitions}]:[{wl.step}:{node.op_name}]"
        )

        # 获取所有依赖节点的 step 索引，用于 load_partition
        dep_parts = [dep.step for dep in dependencies]

        self.logger.info(
            f"✅ 依赖分析： 节点 [{wl.partition + 1}]:[{wl.step}:{node.op_name}] 依赖节点 [{wl.partition + 1}]:{dep_parts}"
        )

        # 配置 storage：设置分片大小和当前分片编号
        storage: DataFlowStorage = copy.copy(node.storage)
        storage.batch_step = wl.partition

        # 断点续传检查：如果输出文件已存在，直接跳过
        # 注意：由于 _check_completed_workloads 已标记已完成任务，
        # 此检查主要用于防御性编程，理论上不会触发
        file_path = storage.write_file_path()
        if storage.file_exists(file_path):
            self.logger.info(
                f"✅ 跳过已存在：{node.op_name} 分片 {wl.partition + 1}/{self._partitions}"
            )
            # 注意：LLM Serving 由调用方（concurrent_execute_operators）的 finally 块释放
            return 0

        # 加载前驱步骤的数据（依赖节点的输出）
        current_partition_df: pd.DataFrame = storage.load_partition(dep_parts)
        storage.current_chunk = current_partition_df

        # 执行 operator 的核心逻辑
        node.op_obj.run(storage=storage, **node.kwargs)
        self.logger.info(
            f"✅ 节点完成：{node.op_name} 分片 {wl.partition + 1}/{self._partitions}"
        )
        return len(current_partition_df)

    def concurrent_execute_operators(
        self,
        max_parallelism: int,
        progress: ProgressInfo,
    ):
        """
        并发执行所有分片的 operators。

        **调度算法**：
        1. **构建依赖图**：为每个 (partition, step) 创建依赖关系
        2. **扫描已完成**：检查已完成任务，减少无效调度
        3. **调度循环**：
           - 选择依赖满足且未执行的 workload（ready_nodes）
           - 按 (partition, -step) 排序，优先执行后面的步骤
           - 提交最多 max_parallelism 个 workload
           - 等待至少一个完成
           - 更新依赖图，释放后续 workload 的依赖
        4. **LLM 互斥**：使用相同 LLM Serving 的 workload 不会同时执行

        Args:
            max_parallelism: 最大并发数（同时执行的线程数）
        """
        self.logger.info(
            f"🚀 启动并发执行 | partitions={self._partitions}, max_parallelism={max_parallelism}, "
            f"total_workload={self._partitions * (len(self.op_nodes_list) - 2)}"
        )

        # 记录已完成的 workload
        completed_workloads: set[Workload] = set()

        # 构建并准备依赖图
        simplified_dependencies, dependencies_checks = (
            self._build_and_prepare_dependencies(completed_workloads)
        )

        for wl in completed_workloads:
            for p in progress["partitions"]:
                if p["id"] == wl.partition:
                    p["completed_steps"] = sorted(
                        list(set(p["completed_steps"] + [wl.step]))
                    )
                    break

        # 总 workload 数：分片数 × (operator 节点数 - 2)
        # 减 2 是因为排除了 DATASET-INPUT 和 DATASET-OUTPUT 节点
        total_workload = self._partitions * (len(self.op_nodes_list) - 2)

        # 【新增】追踪正在被占用的 LLM Serving，确保互斥
        # 使用 id(llm_serving) 作为标识，因为 llm_serving 可能不可 hash
        active_llm_serving_ids: set = set()  # set[id(llm_serving)]

        # 使用线程池并发执行
        with ThreadPoolExecutor(max_workers=max_parallelism) as executor:
            futures: dict[Future, Workload] = {}
            iteration = 0

            # 循环直到所有 workload 完成
            while len(completed_workloads) < total_workload:
                iteration += 1

                # ===== 选择可执行的 workload =====
                # 条件：有 operator + 未完成 + 未提交 + 依赖满足 + LLM 可用
                ready_nodes: list[Workload] = []
                for partition in range(self._partitions):
                    for idx, node in enumerate(self.op_nodes_list):
                        wl = Workload(partition, idx)

                        # 快速跳过：无 operator / 已完成 / 已提交
                        if (
                            node.op_obj is None
                            or wl in completed_workloads
                            or wl in futures.values()
                        ):
                            continue
                        # 检查依赖：排除 DATASET-INPUT（step=0）后，依赖必须为空
                        remaining_deps = dependencies_checks[wl] - set(
                            [Workload(partition, 0)]
                        )
                        if len(remaining_deps) > 0:
                            continue
                        # 检查 LLM Serving 是否正在被占用（互斥）
                        if (
                            node.llm_serving is not None
                            and id(node.llm_serving) in active_llm_serving_ids
                        ):
                            continue
                        ready_nodes.append(wl)

                # 排序策略：优先执行 step 大的（后面的步骤），同 step 时按 partition 升序
                # 原因：后面的步骤依赖前面的步骤，优先执行后面的可以更快释放依赖
                ready_nodes = sorted(
                    ready_nodes,
                    key=lambda wl: (wl.partition, -wl.step),
                )

                # ===== 提交 workload 到线程池 =====
                submitted_count = 0
                max_concurrent = max_parallelism - len(futures)
                for wl in ready_nodes[:max_concurrent]:
                    node = self.op_nodes_list[wl.step]
                    # 提交前再次检查 LLM Serving（防止并发竞争）
                    if (
                        node.llm_serving is not None
                        and id(node.llm_serving) in active_llm_serving_ids
                    ):
                        continue
                    # 占用 LLM Serving
                    if node.llm_serving is not None:
                        active_llm_serving_ids.add(id(node.llm_serving))
                    # 提交任务前，设置 current_steps（表示正在执行）
                    for p in progress.get("partitions", []):
                        if p.get("id") == wl.partition:
                            p["current_steps"].append(wl.step)
                            break

                    # 保存进度
                    self.cache_storage.record_progress(progress)

                    # 提交任务
                    future = executor.submit(
                        self.execute_workload,
                        wl,
                        node,
                        simplified_dependencies[wl],
                    )
                    futures[future] = wl
                    submitted_count += 1

                if submitted_count > 0:
                    self.logger.info(
                        f"📤 {iteration} 提交 {submitted_count} 个 workload | "
                        f"运行中：{len(futures)}, 已完成：{len(completed_workloads)}/{total_workload}"
                    )

                # ===== 等待任务完成 =====
                # 等待至少一个完成，然后重新检查哪些节点可以执行
                if futures:
                    for future in as_completed(futures.keys()):
                        break

                # 收集所有已完成的 future
                done_futures: list[Future] = []
                for future in futures.keys():
                    if future.done():
                        done_futures.append(future)

                # ===== 处理完成的 workload =====
                completed_count = 0
                for future in done_futures:
                    wl = futures.pop(future)
                    node = self.op_nodes_list[wl.step]
                    try:
                        rows_write = future.result()  # 获取结果（可能有异常）
                        completed_workloads.add(wl)
                        completed_count += 1

                        # 更新进度：标记该 partition 的该步骤已完成，清空 current_steps
                        for p in progress.get("partitions", []):
                            if p.get("id") == wl.partition:
                                if "completed_steps" not in p:
                                    p["completed_steps"] = []
                                if wl.step not in p["completed_steps"]:
                                    p["completed_steps"].append(wl.step)
                                    p["completed_steps"].sort()
                                # 清空 current_steps（任务已完成）
                                p["current_steps"].remove(wl.step)
                                p["steps_rows_nums"][wl.step] = rows_write
                                break

                        # 保存进度
                        self.cache_storage.record_progress(progress)
                    except Exception as e:
                        self.logger.error(
                            f"❌ Workload 执行失败 [{wl.partition + 1}/{self._partitions}]:[{wl.step}] | {e}"
                        )
                        raise
                    finally:
                        # 释放 LLM Serving（无论成功还是失败）
                        if node.llm_serving is not None:
                            active_llm_serving_ids.discard(id(node.llm_serving))

                    # 从其他节点的 dependencies 中移除已完成的节点
                    for other_wl in dependencies_checks.keys():
                        dependencies_checks[other_wl].discard(wl)

                if completed_count > 0:
                    self.logger.info(
                        f"✅ 完成 {completed_count} 个 workload | "
                        f"总计：{len(completed_workloads)}/{total_workload} "
                        f"({100 * len(completed_workloads) // total_workload}%)"
                    )

                # 进度检查：每完成 25% 输出一次摘要
                progress_percent = 100 * len(completed_workloads) // total_workload
                if (
                    progress_percent > 0
                    and progress_percent % 25 == 0
                    and progress_percent < 100
                ):
                    self.logger.info(
                        f"📊 进度更新 | {progress_percent}% | "
                        f"进行中：{len(futures)}, 剩余：{total_workload - len(completed_workloads)}"
                    )

        progress["overall_status"] = "completed"
        self.cache_storage.record_progress(progress)
        self.logger.info(f"✨ 并发执行完成 | 总计 {total_workload} 个任务")
