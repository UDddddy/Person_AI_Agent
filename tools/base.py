import json
from pydantic import BaseModel

class BaseTool(BaseModel):

    name :str=""
    description :str=""
    parameters :dict={}

    def run(self, **kwargs):
        raise NotImplementedError("Subclasses must implement the run method.")

    def execute(self,arguments):
        if isinstance(arguments,str):
            try:
                argument = json.loads(arguments)
            except json.JSONDecodeError:
                return ValueError("JSON 解析失败")
        else:
            argument = arguments
        
        try:    
            result = self.run(**argument)
            return str(result)
        except ZeroDivisionError as e:
            return ValueError(f"计算错误分母不能为零: {str(e)}")
        except TypeError as e:
            return ValueError(f"参数类型错误: {str(e)}")
        except Exception as e:
            return ValueError(f"计算错误: {str(e)}")

                

    def schema(self):
        return {
            "type": "function",
            "function":{
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }