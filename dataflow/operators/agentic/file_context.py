# 文件格式到 prompt 类别的映射
format_to_category = {
    # 表格类
    "csv": "table",
    "xlsx": "table",
    "xls": "table",
    # 文档类
    "pdf": "document",
    "docx": "document",
    "doc": "document",
    "md": "document",  # Markdown 作为文档
    # 演示文稿类
    "pptx": "presentation",
    "ppt": "presentation",
    # 结构化数据类
    "json": "structured",
    "xml": "structured",
    "html": "structured",  # HTML 作为结构化数据
    "yaml": "structured",  # YAML 作为结构化数据
    "yml": "structured",  # YML 作为结构化数据
    # 文本类
    "txt": "text",
    "log": "text",  # 日志文件
    # 代码类
    "py": "code",  # Python
    "js": "code",  # JavaScript
    "ts": "code",  # TypeScript
}

prompts = {
    "table": """你是一名测试内容生成器。根据用户的问题/场景，为表格格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。支持 CSV、XLSX 和 XLS 格式。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构:**
```json
{
  "filename": "{filename}",
  "format": "xlsx 或 csv 或 xls",
  "sheets": [
    {
      "name": "工作表名",
      "headers": ["列 1", "列 2", "列 3"],
      "rows": [["数据 1", "数据 2", "数据 3"], [...]]
    }
  ]
}
```

**示例:**
```json
{
  "filename": "test_sales_001.xlsx",
  "format": "xlsx",
  "sheets": [
    {
      "name": "1 月销售数据",
      "headers": ["日期", "产品", "数量", "单价", "销售额"],
      "rows": [
        ["2026-01-05", "无线鼠标", 150, 89.00, 13350.00],
        ["2026-01-08", "机械键盘", 78, 459.00, 35802.00],
        ["2026-01-12", "USB 集线器", 200, 45.00, 9000.00],
        ["2026-01-15", "显示器支架", 45, 299.00, 13455.00],
        ["2026-01-18", "降噪耳机", 120, 699.00, 83880.00]
      ]
    }
  ]
}
```

现在请根据用户问题生成表格内容，返回纯 JSON。""",
    "document": """你是一名测试内容生成器。根据用户的问题/场景，为文档格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。支持 PDF、DOCX、DOC 和 Markdown (.md) 格式。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构:**
```json
{
  "filename": "{filename}",
  "format": "pdf 或 docx 或 doc 或 md",
  "title": "文档标题",
  "sections": [
    {"heading": "章节标题", "paragraphs": ["段落 1", "段落 2"]}
  ],
  "table": {
    "headers": ["列 1", "列 2", "列 3"],
    "rows": [["数据 1", "数据 2", "数据 3"], [...]]
  }
}
```

**PDF/DOCX 示例:**
```json
{
  "filename": "test_report_001.pdf",
  "format": "pdf",
  "title": "2026 年第一季度项目进度报告",
  "sections": [
    {
      "heading": "一、项目概述",
      "paragraphs": [
        "本报告总结了 2026 年第一季度的项目进展情况。在此期间，团队完成了核心功能的开发和测试工作。",
        "项目整体进度符合预期，关键里程碑均已按时交付。"
      ]
    },
    {
      "heading": "二、主要成果",
      "paragraphs": [
        "完成了用户管理模块的重构，性能提升 40%。",
        "新增了数据导出功能，支持多种格式。"
      ]
    }
  ],
  "table": {
    "headers": ["模块", "完成度", "负责人"],
    "rows": [
      ["用户管理", "100%", "张三"],
      ["数据导出", "100%", "李四"],
      ["权限系统", "85%", "王五"]
    ]
  }
}
```

**Markdown 示例:**
```json
{
  "filename": "README.md",
  "format": "md",
  "title": "项目 README",
  "sections": [
    {
      "heading": "## 项目简介",
      "paragraphs": [
        "这是一个用于数据处理的 Python 项目。",
        "支持 CSV、Excel 等多种数据格式的读取和转换。"
      ]
    },
    {
      "heading": "## 安装方法",
      "paragraphs": [
        "使用 pip 安装：`pip install data-processor`",
        "或者从源码安装：`pip install -e .`"
      ]
    },
    {
      "heading": "## 使用示例",
      "paragraphs": [
        "```python",
        "from data_processor import Processor",
        "processor = Processor()",
        "processor.load('data.csv')",
        "processor.process()",
        "```"
      ]
    }
  ],
  "table": {
    "headers": ["功能", "状态", "版本"],
    "rows": [
      ["CSV 导入", "已完成", "v1.0"],
      ["Excel 导出", "已完成", "v1.0"],
      ["数据转换", "进行中", "v1.1"]
    ]
  }
}
```

现在请根据用户问题生成文档内容，返回纯 JSON。""",
    "presentation": """你是一名测试内容生成器。根据用户的问题/场景，为演示文稿格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。支持 PPTX 和 PPT 格式。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构:**
```json
{
  "filename": "{filename}",
  "format": "pptx 或 ppt",
  "title": "演示文稿标题",
  "slides": [
    {"layout": "title", "title": "首页标题", "subtitle": "副标题"},
    {"layout": "content", "title": "页面标题", "bullets": ["要点 1", "要点 2"]},
    {"layout": "table", "title": "数据表", "table": {"headers": ["A", "B"], "rows": [[1, 2]]}}
  ]
}
```

**示例:**
```json
{
  "filename": "test_presentation_001.pptx",
  "title": "2026 年度产品发布会",
  "slides": [
    {"layout": "title", "title": "2026 年度产品发布会", "subtitle": "创新·连接·未来"},
    {"layout": "content", "title": "年度回顾", "bullets": ["用户增长 150%", "产品迭代 12 次", "获得 3 项专利"]},
    {"layout": "content", "title": "新产品发布", "bullets": ["智能助手 Pro 版", "企业协作套件", "数据分析平台"]},
    {"layout": "table", "title": "季度营收", "table": {"headers": ["季度", "营收 (万元)", "增长率"], "rows": [["Q1", "2800", "+25%"], ["Q2", "3200", "+14%"]]}}
  ]
}
```

现在请根据用户问题生成 PPTX 内容，返回纯 JSON。""",
    "structured": """你是一名测试内容生成器。根据用户的问题/场景，为结构化数据格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。支持 JSON、XML、HTML、YAML 和 YML 格式。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构 (JSON 格式):**
```json
{
  "filename": "{filename}",
  "format": "json 或 xml 或 html 或 yaml 或 yml",
  "data": {
    "level1": {
      "level2": {
        "level3": "深层数据",
        "array": [1, 2, 3]
      }
    }
  }
}
```

**内容结构 (XML 格式):**
```json
{
  "filename": "文件名.xml",
  "format": "xml",
  "root": "根标签名",
  "elements": [
    {"tag": "标签", "attrs": {"属性名": "属性值"}, "text": "文本内容", "children": [...]}
  ]
}
```

**内容结构 (HTML 格式):**
```json
{
  "filename": "文件名.html",
  "format": "html",
  "title": "页面标题",
  "head": {"meta": [...], "styles": "..."},
  "body": "<div>页面内容</div>"
}
```

**内容结构 (YAML 格式):**
```json
{
  "filename": "文件名.yaml",
  "format": "yaml",
  "content": "key: value\\nlist:\\n  - item1\\n  - item2"
}
```

**JSON 示例:**
```json
{
  "filename": "test_api_response_001.json",
  "format": "json",
  "data": {
    "request_id": "req_20260401_001",
    "timestamp": "2026-04-01T12:00:00+08:00",
    "user": {
      "id": "U12345",
      "profile": {
        "name": "测试用户",
        "email": "test@example.com",
        "preferences": {
          "language": "zh-CN",
          "timezone": "Asia/Shanghai",
          "notifications": {
            "email": true,
            "sms": false
          }
        }
      },
      "orders": [
        {"order_id": "ORD001", "amount": 299.00, "status": "completed"},
        {"order_id": "ORD002", "amount": 159.00, "status": "pending"}
      ]
    }
  }
}
```

**XML 示例:**
```json
{
  "filename": "test_config_001.xml",
  "format": "xml",
  "root": "configuration",
  "elements": [
    {
      "tag": "database",
      "attrs": {"type": "mysql"},
      "children": [
        {"tag": "host", "text": "192.168.1.100"},
        {"tag": "port", "text": "3306"},
        {"tag": "name", "text": "test_db"}
      ]
    },
    {
      "tag": "logging",
      "attrs": {"level": "info"},
      "children": [
        {"tag": "file", "text": "/var/log/app.log"},
        {"tag": "max_size", "text": "10MB"}
      ]
    }
  ]
}
```

**YAML 示例:**
```json
{
  "filename": "config.yaml",
  "format": "yaml",
  "content": "database:\\n  host: localhost\\n  port: 3306\\n  name: myapp\\nlogging:\\n  level: info\\n  file: /var/log/app.log"
}
```

现在请根据用户问题生成结构化数据内容，返回纯 JSON。""",
    "text": """你是一名测试内容生成器。根据用户的问题/场景，为文本/日志格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。支持 TXT (.txt) 和日志 (.log) 格式。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构:**
```json
{
  "filename": "{filename}",
  "format": "txt 或 log",
  "lines": ["行 1", "行 2", "行 3", ...]
}
```

**示例 (TXT):**
```json
{
  "filename": "{filename}",
  "format": "txt",
  "lines": [
    "系统启动成功",
    "用户登录：user_12345",
    "请求完成：/api/users"
  ]
}
```

**示例 (LOG):**
```json
{
  "filename": "{filename}",
  "format": "log",
  "lines": [
    "[2026-04-01 10:00:01] INFO  Application started",
    "[2026-04-01 10:00:02] INFO  Database connection established",
    "[2026-04-01 10:00:05] INFO  User login: user_12345",
    "[2026-04-01 10:01:30] WARN  Slow query detected (2.3s)",
    "[2026-04-01 10:05:00] ERROR Connection timeout: external_service"
  ]
}
```

现在请根据用户问题生成文本内容，返回纯 JSON。注意：format 字段必须与文件扩展名一致（.txt 用 "txt"，.log 用 "log"）。""",
    "code": """你是一名测试内容生成器。根据用户的问题/场景，为代码格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。支持 Python (.py)、JavaScript (.js) 和 TypeScript (.ts) 格式。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构:**
```json
{
  "filename": "{filename}",
  "format": "py 或 js 或 ts",
  "language": "python 或 javascript 或 typescript",
  "code": "// 代码内容，支持多行\\n// 换行"
}
```

**Python 示例:**
```json
{
  "filename": "data_processor.py",
  "format": "py",
  "language": "python",
  "code": "import pandas as pd\\n\\n# 读取数据\\ndf = pd.read_csv('data.csv')\\n\\n# 数据清洗\\ndf = df.dropna()\\ndf = df[df['value'] > 0]\\n\\n# 保存结果\\ndf.to_csv('cleaned_data.csv', index=False)\\n\\nprint(f'处理完成，共{len(df)}条记录')"
}
```

**JavaScript 示例:**
```json
{
  "filename": "api_client.js",
  "format": "js",
  "language": "javascript",
  "code": "const axios = require('axios');\\n\\n// API 客户端\\nclass APIClient {\\n  constructor(baseUrl) {\\n    this.baseURL = baseUrl;\\n  }\\n\\n  async get(endpoint) {\\n    const response = await axios.get(this.baseURL + endpoint);\\n    return response.data;\\n  }\\n}\\n\\nmodule.exports = APIClient;"
}
```

**TypeScript 示例:**
```json
{
  "filename": "user_service.ts",
  "format": "ts",
  "language": "typescript",
  "code": "interface User {\\n  id: number;\\n  name: string;\\n  email: string;\\n}\\n\\nclass UserService {\\n  private users: User[] = [];\\n\\n  addUser(user: User): void {\\n    this.users.push(user);\\n  }\\n\\n  getUserById(id: number): User | undefined {\\n    return this.users.find(u => u.id === id);\\n  }\\n}\\n\\nexport default UserService;"
}
```

现在请根据用户问题生成代码内容，返回纯 JSON。""",
}


import json

from collections import defaultdict
from pathlib import Path

import pandas as pd

from dataflow import get_logger
from dataflow.core import OperatorABC, LLMServingABC
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage


@OPERATOR_REGISTRY.register()
class FileContextGenerator(OperatorABC):
    def __init__(self, llm_serving: LLMServingABC):
        self.llm_serving = llm_serving
        self.logger = get_logger()

    def run_dataframe(
        self,
        dataframe: pd.DataFrame,
        input_files_key: str,
        input_question_key: str,
        output_key: str,
    ):
        rows = dataframe.to_dict("records")
        self.logger.info(
            f"FileContextGenerator 输入：{len(rows)} rows, input_files_key={input_files_key}, input_question_key={input_question_key}"
        )

        # 初始化输出列为空字典
        for row in rows:
            row[output_key] = {}

        # 收集需要生成的文件
        gen_files: list[tuple[int, str, str]] = []
        row_has_failure = defaultdict(bool)
        invalid_filenames = defaultdict(list)

        for row_idx, row in enumerate(rows):
            # 获取文件列表，支持多种输入格式
            files_list = row.get(input_files_key, [])

            self.logger.debug(
                f"Row {row_idx}: {input_files_key} = {files_list} (type={type(files_list).__name__})"
            )

            # 确保是列表格式
            if isinstance(files_list, str):
                files_list = [files_list]
            elif not isinstance(files_list, list):
                self.logger.warning(
                    f"Row {row_idx}: {input_files_key} 不是列表类型，转换为空列表"
                )
                files_list = []

            # 如果没有文件，跳过
            if not files_list:
                self.logger.warning(f"Row {row_idx}: {input_files_key} 为空，跳过")
                continue

            question = row.get(input_question_key, "")
            self.logger.debug(
                f"Row {row_idx}: question = {question[:100]}..."
                if len(question) > 100
                else f"Row {row_idx}: question = {question}"
            )

            # 先检查这个 row 是否有不合法的文件
            row_valid = True
            for filename in files_list:
                # 验证 filename 必须以 /workspace/ 开头
                if not filename.startswith("/workspace/"):
                    invalid_filenames[row_idx].append(filename)
                    row_valid = False
                    self.logger.error(
                        f"Row {row_idx}: 无效的 filename: {filename} (必须以 /workspace/ 开头)"
                    )
                    break

                # 检查格式是否支持
                ext = Path(filename).suffix[1:].lower()
                if ext not in format_to_category:
                    invalid_filenames[row_idx].append(filename)
                    self.logger.warning(
                        f"Row {row_idx}: 不支持的格式 {ext} - {filename}"
                    )
                    row_valid = False
                    break

            # 只有 row 完全合法，才将所有文件添加到 gen_files
            if row_valid:
                for filename in files_list:
                    gen_files.append((row_idx, question, filename))
                    self.logger.info(f"Row {row_idx}: 添加文件到生成队列：{filename}")
            else:
                row_has_failure[row_idx] = True

        # 记录无效的 filename
        for row_idx, filenames in invalid_filenames.items():
            self.logger.error(
                f"Row {row_idx} 有无效的 filename（必须以 /workspace/ 开头）: {filenames}"
            )

        # 如果没有有效文件，直接返回
        if not gen_files:
            self.logger.warning(
                f"没有有效文件需要生成，gen_files 为空，共检查了 {len(rows)} rows"
            )
            return pd.DataFrame(
                [r for idx, r in enumerate(rows) if not row_has_failure[idx]]
            )

        self.logger.info(f"共 {len(gen_files)} 个文件需要生成内容")

        # 为每个文件生成 prompt
        llm_prompts: list[str] = []
        for row_idx, question, filename in gen_files:
            ext = Path(filename).suffix[1:].lower()
            cat = format_to_category[ext]
            prompt = prompts[cat]
            prompt = prompt.replace("{question}", question)
            prompt = prompt.replace("{filename}", filename)
            llm_prompts.append(prompt)
            self.logger.debug(
                f"Row {row_idx}: 为 {filename} (格式={ext}, 类别={cat}) 生成 prompt"
            )

        self.logger.info(f"准备调用 LLM，共 {len(llm_prompts)} 个 prompt")

        # 调用 LLM 生成内容
        try:
            outputs = self.llm_serving.generate_from_input(
                user_inputs=llm_prompts,
                system_prompt="",
            )
            self.logger.info(f"LLM 调用完成，返回 {len(outputs)} 个结果")
        except Exception as e:
            self.logger.error(f"LLM 调用失败: {e}")
            for row_idx, _, _ in gen_files:
                row_has_failure[row_idx] = True
            return pd.DataFrame(
                [r for idx, r in enumerate(rows) if not row_has_failure[idx]]
            )

        # 按 row_idx 分组处理结果
        row_results = defaultdict(dict)

        for (row_idx, _, filename), output in zip(gen_files, outputs):
            if row_has_failure[row_idx]:
                continue

            self.logger.debug(f"处理结果：{filename}, 输出长度={len(output)}")

            # 清理 LLM 返回的 JSON 字符串（去除 markdown 代码块标记）
            json_string = output.strip(" \n")
            json_string = (
                json_string.removeprefix("```json")
                .removeprefix("```")
                .removesuffix("```")
                .removesuffix("```")
            )

            # 解析 LLM 返回的 JSON
            try:
                content_data = json.loads(json_string)
                self.logger.info(f"成功解析 {filename} 的 JSON 内容")
            except json.JSONDecodeError as e:
                self.logger.error(f"解析 JSON 失败 {filename}: {e}")
                self.logger.error(f"原始输出：{output[:500]}")
                row_has_failure[row_idx] = True
                continue

            row_results[row_idx][filename] = content_data

        # 将成功的结果写回 rows
        for row_idx, file_contents in row_results.items():
            if row_has_failure[row_idx]:
                self.logger.warning(f"Row {row_idx} 有文件合成失败，已丢弃整个 row")
                continue

            rows[row_idx][output_key].update(file_contents)
            self.logger.info(f"Row {row_idx}: 成功生成 {len(file_contents)} 个文件内容")

        # 过滤掉有失败的 row
        valid_rows = [row for idx, row in enumerate(rows) if not row_has_failure[idx]]

        self.logger.info(f"文件内容生成完成：{len(valid_rows)}/{len(rows)} rows 有效")
        return pd.DataFrame(valid_rows)

    def run(
        self,
        storage: DataFlowStorage,
        input_files_key: str,
        input_question_key: str,
        output_key: str,
    ) -> str:
        dataframe: pd.DataFrame = storage.read("dataframe")

        result = self.run_dataframe(
            dataframe,
            input_files_key,
            input_question_key,
            output_key,
        )

        storage.write(result)

        return output_key
