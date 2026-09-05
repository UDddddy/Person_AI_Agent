"""阶段5补 · ToolPipeline 与全工具冒烟测试。

覆盖：
- 所有注册工具都能经 pipeline 正常执行（防 time_tool 缺 required 这类回归）
- 工具不存在 / 参数不完整的友好错误
- before_hook 拦截链生效
- 审计日志 [Before]/[After]/[Blocked] 完整
"""

from tools.pipeline import ToolPipeline
from tools.registry import Tools


def test_all_tools_run_through_pipeline(tmp_path):
    """全工具冒烟：每个注册工具用合法参数过一遍 pipeline，不崩且返回字符串。"""
    tmp_file = tmp_path / "smoke.txt"
    samples = {
        "calculator": {"expression": "1+1"},
        "get_current_time": {},
        "run_command": {"command": "echo hi"},
        "write_file": {"path": str(tmp_file), "content": "hello"},
        "read_file": {"path": str(tmp_file)},
        "edit_file": {
            "path": str(tmp_file),
            "old_string": "hello",
            "new_string": "hi",
        },
    }
    p = ToolPipeline()
    for name, args in samples.items():
        assert name in Tools, f"工具 {name} 未注册"
        result = p.execute(name, args)
        assert isinstance(result, str), f"{name} 返回非字符串: {type(result)}"


def test_tool_not_found():
    """工具不存在 → 友好错误，不抛异常。"""
    assert ToolPipeline().execute("nonexistent", {}) == "工具不存在"


def test_missing_required_args():
    """必填参数缺失 → 友好错误，不抛异常。"""
    assert ToolPipeline().execute("calculator", {}) == "参数不完整"


def test_before_hook_blocks_dangerous_tool():
    """before_hook 拦截危险工具，返回拦截原因，工具不执行；安全工具放行。"""
    def danger_hook(name, args):
        if name == "run_command":
            return "run_command 被拦截"
        return None

    p = ToolPipeline(before_hook=danger_hook)
    assert p.execute("run_command", {"command": "rm -rf /"}) == "run_command 被拦截"
    assert p.execute("calculator", {"expression": "2+3"}) == "5"


def test_pipeline_logs_record_before_after():
    """审计日志：正常执行有 [Before]/[After]。"""
    p = ToolPipeline()
    p.execute("calculator", {"expression": "1+1"})
    assert any("[Before]" in log for log in p.logs)
    assert any("[After]" in log for log in p.logs)


def test_pipeline_logs_record_blocked():
    """审计日志：被拦截的工具有 [Blocked]，没有 [After]。"""
    p = ToolPipeline(before_hook=lambda name, args: "拦截")
    p.execute("calculator", {"expression": "1+1"})
    assert any("[Blocked]" in log for log in p.logs)
    assert not any("[After]" in log for log in p.logs)
