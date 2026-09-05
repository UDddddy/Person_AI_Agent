"""自定义工具示例：文本哈希计算。

接入一个新工具只需要三步（本文件即第 1 步）：
1. 写一个 BaseTool 子类：name / description / parameters（JSON Schema）+ run()；
2. 在 tools/registry.py 里 register_tool(HashTextTool())；
3. 重启应用 —— LLM 通过 TOOL_SCHEMA 自动看到新工具，无需改任何其他代码。

parameters 写法要点（OpenAI function-calling 格式）：
- properties 里每个参数给 type + description（description 直接影响 LLM 会不会用、怎么用）；
- required 列出必填参数 —— ToolPipeline.execute 会先校验必填再执行；
- enum 约束取值范围，能有效防止 LLM 传错参数。
"""

import hashlib

from tools.base import BaseTool


class HashTextTool(BaseTool):
    name: str = "hash_text"
    description: str = "计算文本的哈希值（支持 md5 / sha1 / sha256），用于校验内容一致性或生成摘要指纹"
    parameters: dict = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要计算哈希的文本内容",
            },
            "algorithm": {
                "type": "string",
                "enum": ["md5", "sha1", "sha256"],
                "description": "哈希算法，默认 sha256",
            },
        },
        "required": ["text"],
    }

    def run(self, text: str, algorithm: str = "sha256") -> str:
        digest = hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
        return f"{algorithm}: {digest}"
