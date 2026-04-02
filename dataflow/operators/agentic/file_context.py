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
    "text": """你是一名测试内容生成器。根据用户的问题/场景，为 TXT 格式生成真实感的内容数据，稍后会由 Python 脚本封装成真实二进制文件。

**用户问题/场景:** {question}

**输出文件名:** {filename}

**输出格式:** 纯 JSON，不要额外解释

**内容结构:**
```json
{
  "filename": "{filename}",
  "lines": ["行 1", "行 2", "行 3", ...]
}
```

**示例:**
```json
{
  "filename": "{filename}",
  "lines": [
    "[2026-04-01 10:00:01] INFO  Application started",
    "[2026-04-01 10:00:02] INFO  Database connection established",
    "[2026-04-01 10:00:05] INFO  User login: user_12345",
    "[2026-04-01 10:01:30] WARN  Slow query detected (2.3s)",
    "[2026-04-01 10:02:15] INFO  Request completed: /api/users",
    "[2026-04-01 10:05:00] ERROR Connection timeout: external_service",
    "[2026-04-01 10:05:01] INFO  Retry attempt 1/3",
    "[2026-04-01 10:05:03] INFO  Connection restored"
  ]
}
```

现在请根据用户问题生成 TXT 内容，返回纯 JSON。""",
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

        # 收集需要生成的文件，验证 filename 格式
        gen_files: list[tuple[int, str, str]] = []
        invalid_filenames = defaultdict(list)
        row_has_failure = defaultdict(bool)

        for row_idx, row in enumerate(rows):
            row[output_key] = {}
            row_gen_files: list[tuple[int, str, str]] = []
            for x in row[input_files_key]:
                # 验证 filename 必须以 /workspace/ 开头
                if not x.startswith("/workspace/"):
                    invalid_filenames[row_idx].append(x)
                    row_has_failure[row_idx] = True
                    break  # 这个 row 已经有无效的 filename，跳过其他文件
                row_gen_files.append((row_idx, row[input_question_key], x))
            if not row_has_failure[row_idx]:
                gen_files.extend(row_gen_files)

        # 记录无效的 filename
        for row_idx, filenames in invalid_filenames.items():
            self.logger.error(
                f"Row {row_idx} 有无效的 filename（必须以 /workspace/ 开头）: {filenames}"
            )

        llm_prompts: list[str] = []
        for row_idx, question, filename in gen_files:
            ext = Path(filename).suffix[1:].lower()
            if ext not in format_to_category:
                self.logger.warning(f"Row {row_idx}: 不支持的格式 {ext} - {filename}")
                row_has_failure[row_idx] = True
                break
            cat = format_to_category[ext]
            prompt = prompts[cat]
            prompt = prompt.replace("{question}", question)
            prompt = prompt.replace("{filename}", filename)
            llm_prompts.append(prompt)

        outputs = self.llm_serving.generate_from_input(
            user_inputs=llm_prompts,
            system_prompt="",
        )

        # 按 row_idx 分组处理结果
        row_results = defaultdict(dict)

        for (row_idx, _, filename), output in zip(gen_files, outputs):
            # 清理 LLM 返回的 JSON 字符串（去除 markdown 代码块标记）
            json_string = output.strip(" \n")
            json_string = (
                json_string.removeprefix("```json")
                .removeprefix("```")
                .removesuffix("```")
            )

            # 解析 LLM 返回的 JSON
            try:
                content_data = json.loads(json_string)
            except json.JSONDecodeError as e:
                self.logger.error(f"解析 JSON 失败 {filename}: {e}")
                self.logger.error(f"原始输出：{output[:500]}")
                row_has_failure[row_idx] = True
                continue

            row_results[row_idx][filename] = content_data

        # 将成功的结果写回 rows，跳过有失败的 row
        for row_idx, file_contents in row_results.items():
            if row_has_failure[row_idx]:
                self.logger.warning(f"Row {row_idx} 有文件合成失败，已丢弃整个 row")
                continue

            rows[row_idx][output_key].update(file_contents)

        # 过滤掉有失败的 row
        rows = [row for idx, row in enumerate(rows) if not row_has_failure[idx]]

        return pd.DataFrame(rows)

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
