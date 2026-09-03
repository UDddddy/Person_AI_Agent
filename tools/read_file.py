from pathlib import Path
from tools.base import BaseTool

class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = "读取指定文件的全部内容"
    parameters: dict = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "要读取的文件路径"}},
        "required": ["path"],
    }
    def run(self, path):
        return Path(path).read_text(encoding="utf-8")