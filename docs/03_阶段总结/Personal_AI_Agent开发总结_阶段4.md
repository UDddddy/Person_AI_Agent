# Personal AI Agent 开发总结 · 阶段 4

**阶段名称**：V4 原子工具化 + 工具执行管道（ToolPipeline）
**完成时间**：2026-09-03（阶段 4 启动日，主线完成：原子工具 + 三阶段管道 + 钩子 + LangGraph 集成）

---

## 一、阶段目标

把"工具集合"升级为 **4 个原子工具 + 可拦截的三阶段执行管道**：
1. 提炼原子工具四件套：`read_file` / `write_file` / `edit_file` / `run_command`
2. 三阶段流水线 `ToolPipeline`：Prepare（参数校验 + before 钩子）→ Execute（串行/并行）→ Finalize（after 钩子 + 审计）
3. 与 LangGraph 集成：`tool_node` 改走 `ToolPipeline`
4. 场景验证：Agent 组合原子工具完成"读文件 → 修改 → 写回"

## 二、完成情况

| 能力 | 验收结果 |
|---|---|
| 原子工具（read_file / write_file） | ✅ 继承 BaseTool 注册进注册表，共 5 工具；中文读写 UTF-8 验证通过 |
| run_command 危险工具（阶段 3 延续） | ✅ 纳入 DANGEROUS_TOOLS 审批范围 |
| ToolPipeline 三阶段（Prepare/Execute/Finalize） | ✅ 必填参数校验 + 审计日志 + 耗时统计 |
| before_hook / after_hook 钩子 | ✅ danger_hook 拦截 run_command 生效、calculator 放行（返回 None） |
| ToolNode 集成进 LangGraph | ✅ `graph_agent.py` 工具节点改走 `pipeline.execute`，6 测试无回归 |
| 场景验证（里程碑） | ✅ Agent 组合 read_file + write_file 完成"读 → 改 → 写回"，审计日志完整 |
| 自动化测试 | ✅ `test_graph_agent.py` 6 passed（切换无回归）；阶段 4 暂以 demo 验证为主 |

## 三、代码改动清单

| 文件 | 状态 | 作用 |
|---|---|---|
| `tools/read_file.py` | 🆕 新建 | 原子工具：读取文件（`Path.read_text` + UTF-8） |
| `tools/write_file.py` | 🆕 新建 | 原子工具：写入文件（自动建父目录） |
| `tools/pipeline.py` | 🆕 新建 | ToolPipeline：三阶段执行 + 必填校验 + 审计日志 + 耗时 + before/after 钩子 |
| `tools/registry.py` | ✏️ 修改 | 注册 read_file / write_file（共 5 工具） |
| `app/graph_agent.py` | ✏️ 修改 | `tool_node` 从 `execute_tool` 切到 `pipeline.execute` |
| `demo/demo_dangeroushook.py` | 🆕 新建 | 钩子演示：危险工具拦截（run_command 拦 / calculator 放行） |
| `demo/demo_scene.py` | 🆕 新建 | 场景验证：mock LLM 编排 read_file → write_file 完成副本任务 |
| `tools/executor.py` | 保留 | 旧裸执行器保留（stream_graph 仍用，可作新旧对照） |

## 四、架构（管道版）

```
tool_node（LangGraph 图内）
    └──> ToolPipeline.execute(name, args)
           ├─ Prepare   查工具 → 校验 required → before_hook（返回非 None 即拦截）
           ├─ Execute   调 tool.execute(args)（BaseTool 异常兜底）
           └─ Finalize  记审计日志（参数/结果/耗时）→ after_hook
```

关键设计：
1. **原子工具 vs 场景化工具**：4 个原子能力覆盖"所有文件/命令操作"，Agent 组合出无限行为；工具数量固定，不再随需求线性增长。
2. **横切关注点挂管道**：参数校验、审计、耗时、审批全部放在管道的三个槽位里，不塞进每个工具——工具类保持"只描述能力"，执行策略由管道统一。
3. **钩子约定**：`before_hook(name, args) -> str | None`（返回字符串=拦截原因，None=放行）；拦截后提前 return，Execute / after_hook 自然不执行。
4. **无缝集成**：`pipeline.execute` 与 `execute_tool` 对同一工具返回一致字符串（内部都是 `tool.execute` + `str()`），行为等价 → ToolNode 替换零回归。
5. **实例共享**：`pipeline` 为模块级单例，无状态；`logs` 累积成全局审计日志，天然可观测。

## 五、核心收获

1. **能力供给 vs 需求点杀**：为每个需求写专用工具会爆炸；给 Agent 一套原子能力，让它自己组合，是更可扩展的路线（Pi 架构核心理念之一）。
2. **解耦"做什么"与"怎么做"**：工具只管能力，执行策略（校验/审计/审批）由管道负责。加新能力只加工具 + 注册一行；加新策略只加钩子。
3. **钩子是横切关注点的标准出口**：HITL 审批、日志、拦截都能挂成钩子，为阶段 5 事件总线铺垫。
4. **行为等价替换法**：重构时先保证新旧实现输出一致（对比 `pipeline.execute` vs `execute_tool`），再做无缝替换，用测试兜底验证无回归。
5. **两个环境坑**：① Windows 下 `open()` 默认编码不可靠，读写文件一律显式 `encoding="utf-8"`；② 子目录脚本要 `python -m demo.xxx` 从项目根跑（`sys.path[0]` 是工作目录而非脚本目录）。

## 六、下一步

- HITL 审批挂进管道：把阶段 3 的 `interrupt` 接成 `before_hook`（run_command 走审批，其他放行），打通阶段 3 → 阶段 4
- 多工具串行/并行执行（`ToolPipeline` 支持批量）
- 阶段 4 补正式测试（`test_pipeline.py`）
- 更新 V4 计划文档阶段 4 状态行
- 阶段 5：事件总线（before/after 钩子的系统化延伸）
