from tools.pipeline import ToolPipeline
def danger_hook(name, args):
    """run_command 属于危险操作 → 拦截；其他工具放行。"""
    if name == "run_command":
        return f"⚠️ {name} 需要人工审批，已拦截: {args}"
    return None   # 放行

p = ToolPipeline(before_hook=danger_hook)
print(p.execute("run_command", {"command": "rm -rf /"}))   # 预期: 拦截提示
print(p.execute("calculator", {"expression": "1+1"}))       # 预期: 正常算 2