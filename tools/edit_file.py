from pathlib import Path
from tools.base import BaseTool


class EditFileTool(BaseTool):
    name: str = "edit_file"
    description: str = "在文件中精确替换一段文本（old_string 唯一时替换一处；出现多次需配合 replace_all）"
    parameters: dict = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要编辑的文件路径"},
            "old_string": {"type": "string", "description": "要查找并替换的旧文本"},
            "new_string": {"type": "string", "description": "替换成的新文本"},
            "replace_all": {
                "type": "boolean",
                "description": "是否替换所有出现（默认 false，仅 old_string 唯一时替换）",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, path, old_string, new_string, replace_all=False):
        path = Path(path)
        text = path.read_text(encoding="utf-8")

        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"未在文件中找到待替换文本: {old_string!r}")
        if count > 1 and not replace_all:
            raise ValueError(
                f"待替换文本出现 {count} 次，请指定 replace_all=True 或使用更长的 old_string"
            )

        new_text = text.replace(old_string, new_string)
        path.write_text(new_text, encoding="utf-8")
        return f"已替换 {count} 处: {path}"
