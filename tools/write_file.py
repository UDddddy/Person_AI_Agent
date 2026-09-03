from pathlib import Path
from tools.base import BaseTool


class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = "将内容写入文件"
    parameters: dict = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    }
    def run(self, path, content):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入文件: {path}"