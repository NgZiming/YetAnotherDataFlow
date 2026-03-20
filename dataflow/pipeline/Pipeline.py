"""
Pipeline 模块。

提供数据流管道的核心基类和实现，支持：
- 基础管道执行 (PipelineABC)
- 分片处理与断点续传 (PartitionedPipelineRecoveryABC)
- 流式分批处理 (StreamBatchedPipelineABC)
- 管道可视化 (draw_graph)
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
from tqdm import tqdm

# ==================== 项目本地 ====================
import webbrowser

from dataflow.core import LLM_SERVING_CLASSES
from dataflow.core.operator import OperatorABC
from dataflow.logger import get_logger
from dataflow.pipeline.nodes import KeyNode, OperatorNode
from dataflow.pipeline.plugin import CacheStorage
from dataflow.utils.storage import DataFlowStorage
from dataflow.wrapper.auto_op import AutoOP, OPRuntime


@dataclass(frozen=True)
class Workload:
    partition: int
    step: int


class PipelineABC(ABC):
    def __init__(self, cache_storage: CacheStorage):
        # list of dict, contains `OPRuntime` class and parameters for `operator.run()`
        self.op_runtimes: list[OPRuntime] = []
        self.compiled = False
        # accumulated keys in each operators, index 0 refers to the keys before the first operator
        self.accumulated_keys = (
            []
        )  # list of lists, each sublist contains keys before the operator

        # other items
        self.logger = get_logger()
        self.active_llm_serving = None
        # self.serving_resources = defaultdict(dict)
        # self.serving_reference_counter = Counter()

        self.op_nodes_list: list[OperatorNode] = []
        self.llm_serving_list = []  # list of LLMServing objects
        self.llm_serving_counter = Counter()  # count of LLMServing objects
        self.cache_storage = cache_storage

    @abstractmethod
    def forward(self):
        """
        Main Function to run the pipeline
        """
        pass

    def compile(self):
        self.compiled = True  # flag the pipeline as compiled
        for k, v in vars(self).items():
            if isinstance(v, OperatorABC):
                setattr(self, k, AutoOP(v, k, self))
        self.forward()
        # after call forward, call back function in AutoOP will add the OPRuntime object to self.op_runtimes

        self.forward = self._compiled_forward
        self.logger.info(
            f"Compiling pipeline and validating key integrity "
            f"across {len(self.op_runtimes)} operator runtimes."
        )
        self._build_operator_nodes_graph()
        # self._draw_graph_for_operators()
        # self._build_serving_resources_map()

    def _build_operator_nodes_graph(self):
        """
        Build a graph of operator nodes, each node contains the operator object and its storage.
        """
        for op_runtime in self.op_runtimes:
            llm_serving_obj, storage_obj = None, None
            # get llm_serving object from the operator
            for _, v in vars(op_runtime.op).items():
                if isinstance(v, LLM_SERVING_CLASSES):
                    llm_serving_obj = v
            # get storage object from the function dict
            storage_obj = op_runtime.kwargs.pop("storage", None)

            assert isinstance(
                storage_obj, DataFlowStorage
            ), f"Storage must be a DataFlowStorage object, but got {type(storage_obj)} in {op_runtime}'s `run` function with key `storage`."

            # create an operator node
            op_node = OperatorNode(
                op_obj=op_runtime.op,
                op_name=op_runtime.op_name,
                storage=storage_obj,
                llm_serving=llm_serving_obj,
                **op_runtime.kwargs,
            )

            # append to lists, if None, just keep it
            self.op_nodes_list.append(op_node)
            self.llm_serving_list.append(llm_serving_obj)
            if llm_serving_obj is not None:
                self.llm_serving_counter[llm_serving_obj] += 1
        self.logger.debug(
            f"Built operator nodes graph with {self.op_nodes_list} nodes, \nand {self.llm_serving_list} LLM Serving objects."
        )

        # get keys in the first storage:
        first_op = self.op_nodes_list[0] if self.op_nodes_list else None
        if first_op and first_op.storage:
            iter_storage_keys = first_op.storage.get_keys_from_dataframe()
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

        def _get_op_node_str(self, node: OperatorNode):
            input_keys_string = ""
            for i_key_node in node.input_key_nodes.values():
                input_keys_string += f"\n{i_key_node.key_para_name}={i_key_node.key}"
            output_keys_string = ""
            for o_key_node in node.output_keys_nodes.values():
                output_keys_string += f"\n{o_key_node.key_para_name}={o_key_node.key}"
            # return f"{node.op_name}\n{node.op_obj.__class__.__name__}\n{node.llm_serving.__class__.__name__ if node.llm_serving else 'None'}\n{input_keys_string}\n --- \n{output_keys_string}"
            return f"{node.op_name}\n{node.op_obj.__class__.__name__}\n"

        try:
            import networkx
        except:
            raise ImportError(
                "Please install networkx to draw graph. Please run `pip install networkx[default]`."
            )
        import matplotlib.pyplot as plt

        G = networkx.DiGraph()
        # add OP nodes
        for op_node in self.op_nodes_list:
            G.add_node(op_node, label=_get_op_node_str(self, op_node))
        # add edges between OP nodes
        for op_node in self.op_nodes_list:
            for output_key_nodes in op_node.output_keys_nodes.values():
                for ptr_key_node in output_key_nodes.ptr:
                    G.add_edge(
                        op_node,
                        self.op_nodes_list[ptr_key_node.index],
                        label=ptr_key_node.key,
                    )

        # draw the figure
        pos = networkx.spring_layout(G)
        # pos = networkx.drawing.nx_agraph.graphviz_layout(G, prog='dot')
        # pos = networkx.kamada_kawai_layout(G)

        # pos = networkx.spectral_layout(G)
        # 设置画布大小
        num_nodes = len(G.nodes)
        plt.figure(figsize=(max(10, num_nodes * 0.5), max(8, num_nodes * 0.5)))

        # 绘制图形，使用自定义标签
        labels = {node: data["label"] for node, data in G.nodes(data=True)}
        networkx.draw(
            G,
            pos,
            labels=labels,
            with_labels=True,
            node_size=1000,
            node_shape="s",
            node_color="lightblue",
            edge_color="gray",
            arrows=True,
        )

        # 绘制边的标签
        edge_labels = networkx.get_edge_attributes(G, "label")
        networkx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels)

        # 保存图形
        plt.savefig("operators_graph.png", bbox_inches="tight")
        plt.show()

    def draw_graph(self, port=0, hide_no_changed_keys=True):
        # compile check
        if not self.compiled:
            self.logger.error(
                "Pipeline is not compiled yet. Please call `compile()` before drawing the graph."
            )
            raise RuntimeError(
                "Pipeline is not compiled yet. Please call `compile()` before drawing the graph."
            )
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
                    print(f"🧹 Deleted temp file: {output_html}")
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
            print(f"✅ Graph generated, access it at: {url}")

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
            print(f"❌ Failed to start HTTP service: {e}")
            self.logger.error(f"Failed to start HTTP service: {e}")
        finally:
            os.chdir(orig_cwd)

            # atexit 会负责删除文件，这里无需重复删除
        # # 保存 HTML
        # output_html = "operators_graph.html"
        # net.save_graph(output_html)

        # # 选择端口
        # if port == 0:
        #     sock = socket.socket()
        #     sock.bind(('', 0))
        #     port = sock.getsockname()[1]
        #     sock.close()

        # # 启动 HTTP 服务（主线程）
        # class SilentHandler(SimpleHTTPRequestHandler):
        #     def log_message(self, format, *args):
        #         pass

        # os.chdir(os.path.dirname(os.path.abspath(output_html)))
        # url = f"http://localhost:{port}/{output_html}"
        # print(f"✅ 图已生成，访问: {url}")
        # webbrowser.open(url)

        # # 阻塞运行直到 Ctrl-C
        # try:
        #     with HTTPServer(('0.0.0.0', port), SilentHandler) as httpd:
        #         print(f"HTTP 服务已启动，监听端口 {port}，按 Ctrl-C 退出")
        #         httpd.serve_forever()
        # except KeyboardInterrupt:
        #     print("\n❌ 已退出可视化服务")

    # def _build_serving_resources_map(self):
    #     for op_runtime in self.op_runtimes:
    #         for _, v in vars(op_runtime.op).items():
    #             if isinstance(v, LLMServingABC):
    #                 self.serving_resources[op_runtime.op]["LLMServingABC"] = v
    #                 self.serving_reference_count[v] += 1

    def _compiled_forward(self, resume_from_last: bool = True):
        """
        resume_from_last (bool): if True, resume from the last successful step and batch
        """
        if resume_from_last:
            resume_step, resume_batch, bs = self.cache_storage.get_steps()

            assert resume_batch == 0 and bs == 0
        else:
            resume_step = 0

        # for loop for each op and its `storage` status
        for idx, op_node in enumerate(self.op_nodes_list):
            # resume from a expected step
            if idx - 1 < resume_step:  # minus one since INPUT-DATA Node
                continue

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
                self.cache_storage.record_steps(idx, 0, 0)

            if op_node.llm_serving is not None:
                self.llm_serving_counter[self.active_llm_serving] -= 1
                if self.llm_serving_counter[self.active_llm_serving] == 0:
                    self.logger.debug(
                        f"Detected LLM Serving {self.active_llm_serving} ref reduced to 0, cleaning up..."
                    )
                    self.active_llm_serving.cleanup()
                    self.active_llm_serving = None


class PartitionedPipelineABC(PipelineABC):
    """
    分片管道基类 - 支持将数据集分片并行处理。

    适用于大规模数据集的分布式处理场景，每个分片独立运行管道，
    最后可以合并结果。继承自 PipelineABC，重写了 _compiled_forward
    方法以支持分片执行逻辑。
    """

    def __init__(self, cache_storage: CacheStorage):
        super().__init__(cache_storage)

    def _compiled_forward(self, partitions: int = 1):
        """
        分片模式的管道执行入口。

        按分片顺序执行管道，每个分片处理数据集的一部分。
        支持断点续传，从上次失败的分片继续执行。

        Args:
            partitions: 分片数量

        Note:
            执行进度通过 cache_storage 记录，包含：
            - part: 当前处理的分片编号
            - step: 当前分片内已完成的 operator 步骤
            - part_size: 每个分片的大小（记录数）
        """
        if not self.compiled:
            raise RuntimeError(
                "Pipeline is not compiled yet. Please call `compile()` before running the pipeline."
            )

        part, step, part_size = self.cache_storage.get_steps()

        record_count: int = self.op_nodes_list[1].storage.get_record_count()
        desired_part_size = (record_count + partitions - 1) // partitions
        if part != 0 or step != 0 or part_size != 0:
            assert desired_part_size == part_size
        else:
            part_size = desired_part_size

        self.logger.info(
            f"Resuming partitioned pipeline: part={part}, step={step}, "
            f"part_size={part_size}, total_partitions={partitions}, total_records={record_count}"
        )

        for i in range(part, partitions):
            for idx, op_node in enumerate(self.op_nodes_list[step + 1 :], step + 1):
                if op_node.op_obj is None:
                    continue

                self.logger.info(
                    f"Running {op_node.op_name} | partition={i+1}/{partitions}, "
                    f"step={idx}/{len(self.op_nodes_list)-2}"
                )

                op_node.storage.batch_size = part_size
                op_node.storage.batch_step = i + 1
                current_partition_df = op_node.storage.load_partition([idx - 1])
                op_node.storage.current_streaming_chunk = current_partition_df

                op_node.op_obj.run(storage=op_node.storage, **op_node.kwargs)

                step = idx
                self.cache_storage.record_steps(part, step, part_size)

            part = i + 1
            step = 0
            self.cache_storage.record_steps(part, step, part_size)

        self.logger.info(
            "All partitions completed, cleaning up LLM Serving resources..."
        )
        for op_node in self.op_nodes_list:
            if op_node.llm_serving is not None:
                op_node.llm_serving.cleanup()


class StreamBatchedPipelineABC(PipelineABC):
    def __init__(self, cache_storage: CacheStorage):
        super().__init__(cache_storage)

    def _compiled_forward(
        self, batch_size: int | None = None, resume_from_last: bool = True
    ):
        """
        batch_size (int|None): if set, run the pipeline in batch mode with this batch size
        resume_from_last (bool): if True, resume from the last successful step and batch
        """
        if not self.compiled:
            raise RuntimeError(
                "Pipeline is not compiled yet. Please call `compile()` before running the pipeline."
            )

        if resume_from_last:
            resume_step, resume_batch, bs = self.cache_storage.get_steps()
            assert (
                (resume_step == 0 and resume_batch == 0)
                or (bs == batch_size)
                or (bs == 0 and batch_size is None)
            )
            if bs != 0:
                batch_size = bs
            self.logger.info(
                f"Resuming from last success step {resume_step}, batch step {resume_batch}, batch size {batch_size}."
            )
        else:
            resume_step, resume_batch = 0, 0

        # for loop for each op and its `storage` status
        for idx, op_node in enumerate(self.op_nodes_list):
            # resume from a expected step
            if idx - 1 < resume_step:  # minus one since INPUT-DATA Node
                continue

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
                if batch_size is not None:
                    storage = op_node.storage
                    storage.batch_step = 0 if idx - 1 > resume_step else resume_batch
                    storage.batch_size = batch_size
                    record_count = storage.get_record_count()
                    data_stream = storage.iter_chunks()
                    for _ in range(storage.batch_step):
                        next(data_stream, None)

                RUN_TIMES = (
                    1
                    if batch_size is None
                    else ((record_count - 1) // batch_size + 1) - storage.batch_step
                )
                if batch_size is not None:
                    self.logger.info(
                        f"Pipeline will run for {RUN_TIMES} iterations to cover {record_count} records with batch size {batch_size}."
                    )
                for _ in tqdm(
                    range(RUN_TIMES),
                    desc=f"\033[1;36mRunning {op_node.op_name} with batch size={batch_size}\033[0m",
                    position=0,
                    dynamic_ncols=True,
                    colour="cyan",
                ):
                    if batch_size is not None:
                        try:
                            current_batch_df = next(data_stream)
                            op_node.storage.current_streaming_chunk = current_batch_df
                        except StopIteration:
                            break

                    op_node.op_obj.run(storage=op_node.storage, **op_node.kwargs)
                    if batch_size is not None:
                        op_node.storage.current_streaming_chunk = None
                        op_node.storage.batch_step += 1
                    if resume_from_last:
                        resume_batch = (
                            op_node.storage.batch_step if batch_size is not None else 0
                        )
                        self.cache_storage.record_steps(
                            idx - 1, resume_batch, batch_size or 0
                        )
            if resume_from_last:
                resume_batch = 0  # reset for next op_node
                self.cache_storage.record_steps(idx, resume_batch, batch_size or 0)
            if op_node.llm_serving is not None:
                self.llm_serving_counter[self.active_llm_serving] -= 1
                if self.llm_serving_counter[self.active_llm_serving] == 0:
                    self.logger.debug(
                        f"Detected LLM Serving {self.active_llm_serving} ref reduced to 0, cleaning up..."
                    )
                    self.active_llm_serving.cleanup()
                    self.active_llm_serving = None


class PartitionedPipelineRecoveryABC(PipelineABC):
    """
    带断点续传功能的分片管道基类。

    核心特性：
    1. **分片处理**：将数据集划分为多个分片，按顺序逐个处理
    2. **断点续传**：通过 cache_storage 记录执行进度，中断后可从上次位置恢复
    3. **跳过已完成**：检查输出文件是否存在，已完成的分片自动跳过

    继承自 PipelineABC，重写 _compiled_forward 方法实现分片执行逻辑。

    进度记录说明（通过 cache_storage）：
        - part: 当前已完成的分片编号（从 0 开始）
        - step: 当前分片内已完成的 operator 步骤索引
        - part_size: 每个分片的大小（记录数）
    """

    def __init__(self, cache_storage: CacheStorage):
        """初始化分片管道。

        Args:
            cache_storage: 用于持久化执行进度的存储对象
        """
        super().__init__(cache_storage)

    def _compiled_forward(self, partitions: int = 1):
        """执行分片管道，支持断点续传。

        按分片顺序逐个执行管道，每个分片处理数据集的一部分。
        每次运行前检查缓存进度，从上次中断的位置继续。
        对于每个分片，会检查输出文件是否存在，已完成的自动跳过。

        Args:
            partitions: 分片总数，默认为 1（不分片）

        Raises:
            RuntimeError: 管道未编译时抛出
        """
        if not self.compiled:
            raise RuntimeError(
                "Pipeline is not compiled yet. Please call `compile()` before running the pipeline."
            )

        # 读取上次中断时的进度
        _part, _step, part_size = self.cache_storage.get_steps()

        # 计算分片大小
        record_count: int = self.op_nodes_list[1].storage.get_record_count()
        desired_part_size = (record_count + partitions - 1) // partitions

        # 验证分片大小一致性（如果是恢复运行）
        if _part != 0 or _step != 0 or part_size != 0:
            assert desired_part_size == part_size
        else:
            part_size = desired_part_size

        self.logger.info(
            f"🔄 启动分片管道 | 进度恢复：part={_part}, step={_step}, "
            f"part_size={part_size}, partitions={partitions}, records={record_count}"
        )

        for i in range(0, partitions):
            for idx, op_node in enumerate(self.op_nodes_list[1:], 1):
                if op_node.op_obj is None:
                    continue

                self.logger.info(
                    f"▶️ {op_node.op_name} | 分片 {i+1}/{partitions}, "
                    f"步骤 {idx}/{len(self.op_nodes_list)-2}"
                )

                op_node.storage.batch_size = part_size
                op_node.storage.batch_step = i + 1
                file_path = op_node.storage.write_file_path()
                if op_node.storage.file_exists(file_path):
                    self.logger.info(
                        f"✅ 跳过已存在：{op_node.op_name} 分片 {i+1}/{partitions}"
                    )
                    continue

                current_partition_df = op_node.storage.load_partition([idx - 1])
                op_node.storage.current_streaming_chunk = current_partition_df
                op_node.op_obj.run(storage=op_node.storage, **op_node.kwargs)
                self.cache_storage.record_steps(i, idx, part_size)

            self.cache_storage.record_steps(i + 1, 0, part_size)

        self.logger.info("✨ 所有分片执行完毕，清理 LLM Serving 资源...")
        for op_node in self.op_nodes_list:
            if op_node.llm_serving is not None:
                op_node.llm_serving.cleanup()


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

    def __init__(self, cache_storage: CacheStorage):
        """初始化并行分片管道。

        Args:
            cache_storage: 用于持久化执行进度的存储对象
        """
        super().__init__(cache_storage)

    def _is_dependent_on(
        self,
        wl1: Workload,
        wl2: Workload,
        dependencies: dict[Workload, set[Workload]],
        visited: set[Workload],
    ) -> bool:
        """
        检查 wl1 是否依赖 wl2（直接或间接）。

        Args:
            wl1: 要检查的 workload
            wl2: 目标 workload
            dependencies: 依赖图
            visited: 已访问的 workload 集合（防止循环）

        Returns:
            如果 wl1 依赖 wl2 返回 True
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
        """
        简化依赖关系，消除传递依赖。

        例如：
        - A 依赖 B 和 C
        - B 依赖 C
        简化后：A 只依赖 B（因为通过 B 已经间接依赖 C 了）

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

    def _compiled_forward(self, partitions: int = 1, max_parallelism=4):
        """
        执行入口：初始化进度并启动并发执行。

        Args:
            partitions: 分片总数
            max_parallelism: 最大并发数（同时执行的 workload 数量）

        执行流程：
        1. 计算分片大小
        2. 读取上次进度（如有），初始化为本轮"已完成"状态
        3. 调用 concurrent_execute_operators 启动并发执行
        """
        # 计算总记录数和分片大小
        record_count: int = self.op_nodes_list[1].storage.get_record_count()
        desired_part_size = (record_count + partitions - 1) // partitions

        # 读取上次中断时的进度
        _part, _step, _part_size = self.cache_storage.get_steps()

        # 初始化：如果是全新运行（part=0, step=0），则设为"已完成"状态
        # 这样外部查询进度时会显示 100%，实际执行通过文件检查跳过
        if _part_size == 0:
            _part_size = desired_part_size
        if _step == 0:
            _step = len(self.op_nodes_list) - 2  # 最后一个 operator 步骤
        if _part == 0:
            _part = partitions  # 标记为"所有分片已完成"

        # 断言验证：确保进度记录与预期一致
        assert (
            _part_size == desired_part_size
        ), f"分片大小不一致：{_part_size} != {desired_part_size}"
        assert (
            _step == len(self.op_nodes_list) - 2
        ), f"步骤索引不一致：{_step} != {len(self.op_nodes_list) - 2}"
        assert _part == partitions, f"分片数不一致：{_part} != {partitions}"

        # 写入进度记录（标记为"已完成"）
        self.cache_storage.record_steps(_part, _step, _part_size)
        self._part_size = _part_size

        self.logger.info(
            f"▶️ 执行 PartitionPipelineParallelRun: part:{_part} step:{_step} part_size:{_part_size}"
        )

        # 启动并发执行
        self.concurrent_execute_operators(partitions, max_parallelism)

    def execute_workload(
        self,
        wl: Workload,
        partitions: int,
        node: OperatorNode,
        dependencies: set[Workload],
    ):
        """
        执行单个 Workload（分片 + 步骤组合）。

        Args:
            wl: Workload 对象，包含 partition 和 step 信息
            partitions: 总分片数（用于日志显示）
            node: 要执行的 OperatorNode
            dependencies: 该 workload 的依赖集合（同一 partition 内的前驱步骤）

        执行流程：
        1. 记录执行日志
        2. 分析依赖节点的 step 索引
        3. 配置 storage 的 batch_size 和 batch_step
        4. 检查输出文件是否存在（断点续传）
        5. 加载前驱步骤的数据
        6. 执行 operator.run()
        """
        self.logger.info(
            f"▶️ 执行 [{wl.partition + 1}/{partitions}]:[{wl.step}:{node.op_name}]"
        )

        # 获取所有依赖节点的 step 索引，用于 load_partition
        dep_parts = [dep.step for dep in dependencies]

        self.logger.info(
            f"✅ 依赖分析： 节点 [{wl.partition + 1}]:[{wl.step}:{node.op_name}] 依赖节点 [{wl.partition + 1}]:{dep_parts}"
        )

        # 配置 storage：设置分片大小和当前分片编号
        storage = copy.copy(node.storage)
        storage.batch_size = self._part_size
        storage.batch_step = wl.partition + 1

        # 断点续传检查：如果输出文件已存在，直接跳过
        file_path = storage.write_file_path()
        if storage.file_exists(file_path):
            self.logger.info(
                f"✅ 跳过已存在：{node.op_name} 分片 {wl.partition + 1}/{partitions}"
            )
            return

        # 加载前驱步骤的数据（依赖节点的输出）
        current_partition_df = storage.load_partition(dep_parts)
        storage.current_streaming_chunk = current_partition_df

        # 执行 operator 的核心逻辑
        node.op_obj.run(storage=storage, **node.kwargs)
        self.logger.info(
            f"✅ 节点完成：{node.op_name} 分片 {wl.partition + 1}/{partitions}"
        )

    def concurrent_execute_operators(self, partitions: int, max_parallelism: int):
        """
        并发执行所有分片的 operators。

        Args:
            partitions: 分片总数
            max_parallelism: 最大并发数（同时执行的线程数）

        执行流程：
        1. 构建 workload 依赖图：每个 (partition, step) 是一个 Workload
        2. 识别依赖关系：同一 partition 内，step N 依赖 step N-1
        3. 使用 ThreadPoolExecutor 并发执行
        4. 循环：
           a. 找出所有"依赖已满足且未执行"的 workload（ready_nodes）
           b. 按 step 降序、partition 升序排序（优先执行后面的步骤）
           c. 提交最多 max_parallelism 个 workload 到线程池
           d. 等待至少一个完成
           e. 更新依赖图，释放后续 workload 的依赖
        """
        self.logger.info(
            f"🚀 启动并发执行 | partitions={partitions}, max_parallelism={max_parallelism}, "
            f"total_workload={partitions * (len(self.op_nodes_list) - 2)}"
        )

        # 记录已完成的 workload
        completed_workloads: set[Workload] = set()

        # 构建完整的依赖图：Workload -> set[依赖的 Workload]
        dependencies: dict[Workload, set[Workload]] = dict()

        for partition in range(partitions):
            for idx, node in enumerate(self.op_nodes_list):
                wl = Workload(partition, idx)
                dependencies[wl] = set()
                # 遍历所有输入 key，找到它们的前驱节点
                for _, key_node in node.input_key_nodes.items():
                    # 添加依赖：当前 workload 依赖前驱节点的 workload
                    dependencies[wl].add(Workload(partition, key_node.ptr[-1].index))

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

        # 打印 partition=1 的依赖图（用于调试）
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

        # 用于追踪剩余依赖的副本（执行过程中会修改）
        dependencies_checks = copy.deepcopy(simplified_dependencies)

        # 总 workload 数：分片数 × (operator 节点数 - 2)
        # 减 2 是因为排除了 DATASET-INPUT 和 DATASET-OUTPUT 节点
        total_workload = partitions * (len(self.op_nodes_list) - 2)

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

                # 找到所有可以执行的节点（依赖已满足且未执行）
                ready_nodes: list[Workload] = []
                for partition in range(partitions):
                    for idx, node in enumerate(self.op_nodes_list):
                        wl = Workload(partition, idx)

                        # 跳过没有 operator 的节点（如 DATASET-INPUT/OUTPUT）
                        if node.op_obj is None:
                            continue
                        # 跳过已完成的
                        if wl in completed_workloads:
                            continue
                        # 跳过已提交但未完成的
                        if wl in futures.values():
                            continue
                        # 检查依赖：排除 DATASET-INPUT（step=0）后，依赖必须为空
                        if (
                            len(dependencies_checks[wl] - set([Workload(partition, 0)]))
                            > 0
                        ):
                            continue
                        # 【新增】检查 LLM Serving 是否正在被占用
                        if node.llm_serving is not None:
                            if id(node.llm_serving) in active_llm_serving_ids:
                                # 该 LLM 正在被其他 workload 使用，跳过
                                continue
                        ready_nodes.append(wl)

                # 排序：优先执行 step 大的（后面的步骤），同 step 时按 partition 升序
                ready_nodes = sorted(
                    ready_nodes,
                    key=lambda wl: (wl.partition, -wl.step),
                )

                # 提交可执行的节点（不超过剩余线程数）
                submitted_count = 0
                for wl in ready_nodes[: max_parallelism - len(futures)]:
                    node = self.op_nodes_list[wl.step]
                    # 【新增】在提交前再次检查 LLM Serving 是否被占用
                    # （因为前面的任务可能已经占用了该 LLM）
                    if node.llm_serving is not None:
                        if id(node.llm_serving) in active_llm_serving_ids:
                            # 该 LLM 已被前面提交的任务占用，跳过
                            continue
                        # 【新增】记录该 LLM Serving 正在被使用
                        active_llm_serving_ids.add(id(node.llm_serving))
                    future = executor.submit(
                        self.execute_workload,
                        wl,
                        partitions,
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

                # 等待至少一个完成，然后重新检查哪些节点可以执行
                if futures:
                    for future in as_completed(futures.keys()):
                        break

                # 收集所有已完成的 future
                done_futures: list[Future] = []
                for future in futures.keys():
                    if future.done():
                        done_futures.append(future)

                # 处理完成的 workload
                completed_count = 0
                for future in done_futures:
                    wl = futures.pop(future)
                    try:
                        _ = future.result()  # 获取结果（可能有异常）
                        completed_workloads.add(wl)
                        completed_count += 1
                        # 【新增】释放该 LLM Serving
                        node = self.op_nodes_list[wl.step]
                        if node.llm_serving is not None:
                            active_llm_serving_ids.remove(id(node.llm_serving))
                    except Exception as e:
                        self.logger.error(
                            f"❌ Workload 执行失败 [{wl.partition + 1}/{partitions}]:[{wl.step}] | {e}"
                        )
                        raise

                    # 从其他节点的 dependencies 中移除已完成的节点
                    for other_wl in dependencies_checks.keys():
                        if wl in dependencies_checks[other_wl]:
                            dependencies_checks[other_wl].remove(wl)

                if completed_count > 0:
                    self.logger.info(
                        f"✅ 完成 {completed_count} 个 workload | "
                        f"总计：{len(completed_workloads)}/{total_workload} "
                        f"({100 * len(completed_workloads) // total_workload}%)"
                    )

                # 进度检查：每完成 25% 输出一次摘要
                progress = 100 * len(completed_workloads) // total_workload
                if progress > 0 and progress % 25 == 0 and progress < 100:
                    self.logger.info(
                        f"📊 进度更新 | {progress}% | "
                        f"进行中：{len(futures)}, 剩余：{total_workload - len(completed_workloads)}"
                    )

        self.logger.info(f"✨ 并发执行完成 | 总计 {total_workload} 个 workload")
