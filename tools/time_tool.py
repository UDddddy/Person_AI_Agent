
from datetime import datetime
from tools.base import BaseTool

class GetCurrentTimeTool(BaseTool):
    name: str = "get_current_time"
    description: str = "获取当前时间"  # 你自己想，让 LLM 知道什么时候用它
    parameters: dict = {
        "type": "object",
        "properties": {},   # 这个工具不需要参数
        "required": [],
    }

    def run(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")