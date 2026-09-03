# Personal AI Agent

从零手写的一个 **Personal AI Agent 学习项目**：以"极简核心 + 深度可扩展"为目标，逐步搭建一个可对话、会调工具、能流式输出、可持久化的个人 Agent。

> 📚 **全部学习文档与计划见 [`docs/README.md`](docs/README.md)（文档总索引）**

## 当前进度

```
阶段 0 ✅  FastAPI + LLM 最小闭环
阶段 1 ✅  V1 工程化 Tool Agent（BaseTool + 注册表 + 有界循环）
阶段 2 ✅  V2 多轮对话 + SQLite 记忆
阶段 3 🔄  V3 LangGraph + Checkpoint + SSE 流式 + HITL（进行中）
阶段 4-9   原子工具 / 钩子 / 树形会话压缩 / Provider / 多 Agent / 生产化（未开始）
```

## 目录结构

```
├── app/            # 应用代码（FastAPI 入口、LangGraph 图、配置）
├── demo/           # 命令行联调演示
├── test/           # pytest 自动化测试
├── tools/          # 工具（calculator / time 等）
├── docs/           # ★ 全部文档（计划/阶段笔记/总结/架构学习/每日记录）
│   ├── 01_计划/ 02_阶段笔记/ 03_阶段总结/ 04_架构学习/ daily/ 00_归档/
├── learn_graph.py  # 阶段3 第一课：LangGraph 最小图（亲手写）
├── learn_sse.py    # 阶段3 第二课：SSE 流式（亲手写）
└── requirements.txt
```

## 常用命令（Windows / PowerShell）

```powershell
# 启动 API 服务（SSE 端点：POST /api/chat_graph_stream）
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# 健康检查
Invoke-RestMethod http://127.0.0.1:8000/health

# 运行测试（当前基线：26 passed）
.venv\Scripts\python.exe -m pytest test/ -q

# 命令行演示
.venv\Scripts\python.exe -m demo.demo_stream      # SSE 流式演示
.venv\Scripts\python.exe -m demo.demo_graph_agent # LangGraph 问答
```

## 环境

- Python 3.13 · FastAPI · LangGraph · OpenAI SDK（DeepSeek 兼容）
- 模型配置在 `.env`（`LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY`），由 `app/config.py` 读取
