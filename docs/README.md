# 📚 项目文档索引

> Personal AI Agent 学习项目 · 全部文档统一收录于 `docs/`，按类型分目录。
> 最后更新：2026-09-04 · 9个阶段全部完成 · 145 passed

## 目录结构

```
docs/
├── README.md                  ← 本索引
├── 01_计划/                    ← 开发计划
├── 02_阶段笔记/                 ← 每阶段技术笔记（怎么用、为什么、底层原理）
├── 03_阶段总结/                 ← 每阶段完成总结（验收结果 + 核心收获）
├── 04_架构学习/                 ← 优秀 Agent 项目架构精读笔记
├── 05_学习复盘与面试/            ← ★ 设计复盘 + 面试题（阶段6-9）
├── daily/                      ← 每日学习记录
└── 00_归档/                     ← 已被取代的旧版文档
```

---

## 📋 01_计划

| 文档 | 说明 |
|---|---|
| `Personal_AI_Agent开发计划_V4_Pi架构版.md` | **当前主计划**（V4）：基于 Pi Agent 架构重设计，阶段 0-9 + 缓冲 |

## 🧠 02_阶段笔记

按阶段阅读顺序排列：

| 文档 | 阶段 | 主题 |
|---|---|---|
| `阶段0_FastAPI与LLM最小闭环.md` | 0 | FastAPI + LLM 最小闭环 |
| `阶段1_ToolAgent工程化.md` | 1 | BaseTool + 注册表 + 有界循环 |
| `阶段2_多轮会话与SQLite记忆.md` | 2 | 多轮对话 + SQLite 记忆 |
| `阶段3_LangGraph重构与Checkpoint.md` | 3 | LangGraph 状态图 + Checkpoint |
| `阶段3_手写Loop痛点与LangGraph迁移.md` | 3 | 手写 Loop 的痛点与迁移实践 |

## ✅ 03_阶段总结

| 文档 | 阶段 | 状态 |
|---|---|---|
| `Personal_AI_Agent开发总结_阶段1.md` | 1 | ✅ 完成 |
| `Personal_AI_Agent开发总结_阶段2.md` | 2 | ✅ 完成 |
| `Personal_AI_Agent开发总结_阶段3.md` | 3 | ✅ 完成 |
| `Personal_AI_Agent开发总结_阶段4.md` | 4 | ✅ 完成 |

> 阶段5-9的总结见 `05_学习复盘与面试/`（设计复盘文档，内容更深入）。

## 🔭 04_架构学习

| 文档 | 对象 | 亮点 |
|---|---|---|
| `Pi_Agent_架构设计学习笔记.md` | badlogic/pi-mono | 事件流 + 钩子 + 树形会话 + 压缩 + 70+ provider |
| `Codex_架构设计学习笔记.md` | openai/codex | 三层 token 防线 + 审批链 HITL + 两级压缩 + 澄清 |

## 🎯 05_学习复盘与面试（★ 重点）

每篇包含：问题背景、设计决策、测试手法、设计哲学、面试题。

| 文档 | 阶段 | 核心主题 |
|---|---|---|
| `阶段6_树形会话与Compaction设计复盘.md` | 6 | append-only 树形存储 + 安全切割点压缩 + 工具配对不拆散 |
| `阶段7_Provider抽象层与多模型设计复盘.md` | 7 | LLMProvider 抽象 + 配置驱动切换 + Mock 一等公民 |
| `阶段8_多Agent编排设计复盘.md` | 8 | Chain/Team/Subagent 三种模式 + $INPUT/$ORIGINAL 意图锚定 |
| `阶段9_生产化设计复盘.md` | 9 | MetricsCollector 指标 + EvalRunner 评估 + Docker + 文档 |
| `近期学习总结_2026-09-01至03.md` | — | 阶段5-7学习总结 |
| `面试题库_个人Agent方向.md` | — | 个人 Agent 方向面试题汇总 |

## 📅 daily

每日学习记录（`daily/YYYY-MM-DD_学习记录.md`），可跨天回顾"学到哪、困惑过什么"。

## 🗄 00_归档

已被取代的旧版文档（V1/V2/V3 计划等），仅保留备查，不再更新。

---

## 当前进度速览

```
阶段 0 ✅  FastAPI + LLM 最小闭环
阶段 1 ✅  V1 工程化 Tool Agent
阶段 2 ✅  V2 多轮对话 + SQLite 记忆
阶段 3 ✅  V3 LangGraph + Checkpoint + SSE + HITL
阶段 4 ✅  原子工具 + 工具管道
阶段 5 ✅  EventBus + Hooks + 扩展体系
阶段 6 ✅  树形会话 + Compaction 压缩
阶段 7 ✅  Provider 抽象层 + 多模型
阶段 8 ✅  多 Agent 编排（Chain/Team/Subagent）
阶段 9 ✅  生产化（可观测性/评估/Docker/文档）
全部完成 🎉 · 145 passed
```

> 进度细节与对照见《V4 开发计划》与各阶段复盘文档。
