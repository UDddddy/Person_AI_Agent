from tools.base import BaseTool

class RunCommandTool(BaseTool):
    name:str = "run_command"
    description :str= "运行一个命令"
    parameters:dict = {
        "type":"object",
        "properties":{
            "command":{
                "type":"string",
                "description":"要运行的命令"
            }
        },
        "required":["command"]
    }
    def run(self,command):
        return f"模拟命令执行，命令执行成功{command}"