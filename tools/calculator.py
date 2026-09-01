import ast
from tools.base import BaseTool
max_number = 1000000000
def calculate_node(node):

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.BinOp):
        left = calculate_node(node.left)
        right = calculate_node(node.right)

        if isinstance(node.op, ast.Add):
            return left + right

        elif isinstance(node.op, ast.Sub):
            return left - right

        elif isinstance(node.op, ast.Mult):
            return left * right

        elif isinstance(node.op, ast.Div):
            return left / right
        elif isinstance(node.op,ast.Mod):
            return left % right
        elif isinstance(node.op,ast.Pow):
            return left ** right
        elif isinstance(node.op,ast.FloorDiv):
            return left // right

    if isinstance(node, ast.UnaryOp):

        value = calculate_node(node.operand)

        if isinstance(node.op, ast.USub):
            return -value

        elif isinstance(node.op, ast.UAdd):
            return +value

    raise ValueError("不支持的表达式")
def calculate(expression):
    expression = expression.replace(" ","")

    allow_node = (
        ast.Expression,
        ast.Constant,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.FloorDiv,
        ast.UnaryOp,
        ast.USub,
        ast.UAdd

    )
    try:
        tree = ast.parse(expression,mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node,allow_node):
                return "表达式中存在不支持的节点"
            if isinstance(node,ast.Constant):
                if isinstance(node.value,bool):
                    return "计算数值中存在bool类型"
                if not isinstance(node.value,(int,float)):
                    return "存在非int|float类型的计算数值"
                if abs(node.value) > max_number:
                    return "数字过大无法计算"
                
            if isinstance(node,ast.BinOp):
                if not isinstance(node.op,(ast.Add,
                                            ast.Sub,
                                            ast.Mult,
                                            ast.Div,
                                            ast.Pow,
                                            ast.Mod,
                                            ast.FloorDiv,)):
                    return "表达式不合法"
        return calculate_node(tree.body)
    except ZeroDivisionError:
        return "分母不能为零"
    except SyntaxError:
        return "表达式不合法"
    except ValueError as e:
        return f"错误：{e}"
class CalculatorTool(BaseTool):
    name:str = "calculator"
    description :str= "计算一个算数表达式,支持加减乘除、取模、取余、幂运算"
    parameters:dict = {
        "type":"object",
        "properties":{
            "expression":{
                "type":"string",
                "description":"算数表达式"
            }
        },
        "required":["expression"]

    }
    def run(self,expression):
        return calculate(expression)

if __name__ == "__main__":
    print(calculate("123 + 456"))
    print(calculate("10 * 20"))
    print(calculate("10 / 0"))
    print(calculate("123 + abc"))
    print(calculate("(10 + 20) * 3"))

    print(calculate("10 ** 2"))
    print(calculate("13//10"))
    print(calculate("13%10"))