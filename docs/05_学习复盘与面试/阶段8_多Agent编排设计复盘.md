# 阶段 8 设计复盘：多 Agent 编排（Chain / Team / Subagent）

> 本文是阶段 8 开发完成后的设计决策复盘，既是学习笔记，也是面试讲项目时的核心素材。
> 覆盖：为什么要做多 Agent 编排、三种协作模式的本质区别、
>
> $INPUT/$
>
> ORIGINAL 设计、求职助手主线、测试设计、面试题。



***

## 一、阶段 8 要解决什么问题

阶段 7 之前，整个系统只有**一个 Agent**：用户发消息 → Agent 调 LLM → 可能调工具 → 返回结果。

单 Agent 的问题：



1. **复杂任务不会拆解**：用户说 "帮我准备求职"，单 Agent 要同时做需求分析、技能匹配、质量复核，一个 prompt 塞太多职责，输出质量不稳定。

2. **没有分工**：规划、执行、审核是三种不同能力，混在一个 Agent 里，既不专业也不可控。

3. **无法并行**：查 3 个数据源只能串行，慢。

4. **没有调度**：不同类型的任务应该交给不同专长的 Agent，但单 Agent 只能 "一把梭"。

阶段 8 的解法：**引入三种多 Agent 协作模式**，让 Agent 之间能分工、能调度、能并行。



***

## 二、三种协作模式的本质区别

这是阶段 8 最核心的理解，面试必问。



| 维度     | AgentChain（流水线）               | AgentTeam（调度器）   | Subagent（后台并行） |
| ------ | ----------------------------- | ---------------- | -------------- |
| 执行方式   | 固定顺序，每步都跑                     | 动态选**一个**子 Agent | 多个**并行**跑      |
| 谁决定下一步 | 预定义的 steps 列表                 | 主 Agent 用 LLM 选择 | 调用方一次性提交       |
| 数据传递   | $INPUT（上步输出）+ $ORIGINAL（原始意图） | 任务描述直接传给子 Agent  | 各自独立，无数据传递     |
| 阻塞性    | 同步阻塞，等全部完成                    | 同步阻塞，等选中的子 Agent | 异步并行，主流程不阻塞    |
| 适用场景   | 有明确步骤的任务                      | 任务类型不确定，需要 "派活"  | 独立后台任务         |
| 类比     | 工厂流水线                         | 项目经理派活给工程师       | 同时开多个工人各干各的    |

**一句话区分**：



* Chain = "先做 A 再做 B 再做 C"（顺序固定）

* Team = "这个活该谁干？你来干"（动态选择）

* Subagent = "你们三个同时干，干完通知我"（并行）



***

## 三、AgentChain：顺序流水线

### 3.1 核心设计



```
@dataclass

class ChainStep:

&#x20;   name: str

&#x20;   prompt: str   # 支持 \$INPUT 和 \$ORIGINAL 变量

class AgentChain:

&#x20;   def run(self, original\_input: str) -> ChainResult:

&#x20;       current\_input = original\_input

&#x20;       for step in self.steps:

&#x20;           rendered = \_render\_prompt(step.prompt, current\_input, original\_input)

&#x20;           resp = provider.chat(\[{"role": "user", "content": rendered}])

&#x20;           current\_input = resp.content  # 本步输出 = 下一步 \$INPUT
```

### 3.2 为什么要 $INPUT 和 $ORIGINAL 两个变量

这是 Chain 设计的精髓，也是面试高频考点。

**只有 \$INPUT 的问题**：第 3 步只能看到第 2 步的输出，如果第 2 步丢失了原始需求的某些信息，第 3 步就 "失忆" 了。比如：



* 原始需求："帮我写一篇关于 AI 的文章，要幽默风格"

* 第 1 步输出："文章大纲：1. 介绍 2. 应用 3. 未来"（丢失了 "幽默风格"）

* 第 2 步输出："文章正文..."（按大纲写，没幽默）

* 第 3 步复核：只看到正文，不知道用户要幽默风格

**\$ORIGINAL 的作用**：每一步都能看到用户最初的完整意图，防止多步传递后信息丢失。

**类比**：$INPUT 是"上一道工序的半成品"，$ORIGINAL 是 "客户的原始订单"。工厂里每道工序都要看半成品（知道现在做到哪了），也要看原始订单（知道客户到底要什么）。

### 3.3 为什么用正则替换而不是 str.replace



```
result = re.sub(r"\\\$INPUT\b", input\_text, result)

result = re.sub(r"\\\$ORIGINAL\b", original, result)
```



* `\b` 词边界：避免 `$INPUT` 匹配到 `$INPUT_2` 这种变量名

* 正则替换比 `str.replace` 更安全：如果 `$INPUT` 的内容里恰好包含 `$ORIGINAL` 这个字符串，`str.replace` 会二次替换，正则不会

### 3.4 ChainResult 为什么保留每步中间结果



```
@dataclass

class ChainResult:

&#x20;   final\_output: str

&#x20;   steps: list\[StepResult]  # 每步的 name + input\_text + output
```



* **调试**：哪一步出了问题一目了然

* **可解释性**：用户能看到 "AI 是怎么一步步得出结论的"

* **面试加分**：不是黑盒，每步都可追溯



***

## 四、AgentTeam：调度器模式

### 4.1 核心设计



```
class AgentTeam:

&#x20;   def dispatch(self, task: str) -> DispatchResult:

&#x20;       # 第1步：主 Agent 用 LLM 选择子 Agent

&#x20;       selection\_prompt = "可用子 Agent：\n- planner: 规划\n- builder: 构建\n任务：xxx\n选择："

&#x20;       selected\_name = provider.chat(\[...]).content.strip()

&#x20;       # 第2步：容错匹配

&#x20;       selected\_name = self.\_match\_agent\_name(selected\_name)

&#x20;       # 第3步：子 Agent 执行

&#x20;       agent = self.agents\[selected\_name]

&#x20;       result = provider.chat(\[

&#x20;           {"role": "system", "content": agent.system\_prompt},

&#x20;           {"role": "user", "content": task},

&#x20;       ])
```

### 4.2 为什么需要容错匹配 \_match\_agent\_name

LLM 不一定乖乖只返回名字。实际可能返回：



* `"planner"`（理想情况）

* `"planner."`（带标点）

* `"我选择 planner"`（带多余文字）

* `"Planner"`（大小写不同）

三层匹配策略：



1. **精确匹配**：直接在 agents 字典里找

2. **去标点后匹配**：`rstrip("。.!！")`

3. **包含匹配**：返回文本里包含某个子 Agent 名就选它

这是工程实战中非常重要的细节 ——**永远不要假设 LLM 会严格按你的格式返回**。

### 4.3 Team 和 Chain 的关键区别



* Chain 的步骤是**预定义**的，每步都执行

* Team 的子 Agent 是**动态选择**的，只执行一个

* Chain 适合 "我知道该分几步" 的任务

* Team 适合 "我不知道该找谁，但我知道有哪些专家" 的任务



***

## 五、Subagent：后台并行

### 5.1 什么是 "无头"（headless）

无头子 Agent 的三个特征：



1. **不直接和用户交互**：没有对话界面，只接收任务、返回结果

2. **不维护对话历史**：每次执行是独立的，没有上下文记忆

3. **通过事件上报状态**：用 EventBus 发 start/done/error，主 Agent 订阅事件即可

为什么无头很重要：并行执行时，如果每个子 Agent 都维护对话历史，会出现状态串台（A 的历史混进 B）。无头设计让每个子 Agent 都是无状态的，并行天然安全。

### 5.2 事件上报设计



```
EVENT\_SUBAGENT\_START = "subagent.start"

EVENT\_SUBAGENT\_DONE = "subagent.done"

EVENT\_SUBAGENT\_ERROR = "subagent.error"
```

复用阶段 5 的 EventBus，不引入新的事件系统。主 Agent 或 ObservabilityExtension 订阅这些事件，就能：



* 实时显示进度（"子 Agent A 已完成"）

* 记录结构化日志

* 出错时告警

### 5.3 并行执行：ThreadPoolExecutor



```
class SubagentRunner:

&#x20;   def run\_parallel(self, specs):

&#x20;       with ThreadPoolExecutor(max\_workers=self.max\_workers) as executor:

&#x20;           future\_to\_name = {executor.submit(agent.run): spec.name for spec in specs}

&#x20;           for future in as\_completed(future\_to\_name):

&#x20;               results\[name] = future.result()

&#x20;       return \[results\[spec.name] for spec in specs]  # 按输入顺序返回
```

**为什么结果要按输入顺序返回**：`as_completed` 的完成顺序是不确定的（谁先跑完谁先返回），但调用方通常期望结果顺序和输入顺序一致（方便对应）。所以用 dict 收集，最后按输入顺序重排。

**为什么用线程而不是协程**：



* LLM 调用是 IO 密集型（等网络响应），线程和协程都适合

* 但当前代码用的是同步的 `provider.chat`，用线程最简单，不需要把整个调用链改成 async

* 如果未来 provider 改成 async，再换 asyncio.gather

### 5.4 Subagent 和 Team 的区别



* Team：主 Agent **选一个**子 Agent，同步等结果

* Subagent：**多个**子 Agent 同时跑，通过事件通知，主 Agent 可以继续干别的

类比：



* Team = 你去餐厅，服务员（主 Agent）根据你的口味推荐一个厨师（子 Agent）给你做菜，你等菜

* Subagent = 你同时点了 3 道菜，3 个厨师同时做，每做好一道服务员通知你



***

## 六、求职助手：主线落地

### 6.1 为什么用 Chain 而不是 Team

求职助手的三步是**固定顺序**的：



1. planner：分析 JD，提取要求

2. builder：匹配技能，生成建议

3. reviewer：复核质量

每一步都依赖上一步的输出，且顺序不可调换（不能先复核再分析）。所以用 Chain 最合适。

如果用 Team，主 Agent 每次都要 "决定下一步该谁干"，反而增加了不确定性和 LLM 调用次数。

### 6.2 \$ORIGINAL 在求职助手里的实际价值



* planner 看到完整 JD

* builder 看到 planner 的分析（$INPUT）+ 完整 JD 和求职者技能（$ORIGINAL）

* reviewer 看到 builder 的建议（$INPUT）+ 完整 JD 和求职者技能（$ORIGINAL）

如果没有 \$ORIGINAL，reviewer 只能看到 builder 的建议，可能遗漏了 JD 里的某些要求，导致复核不全面。



***

## 七、测试设计

阶段 8 测试在 `test/test_stage8.py`，共 21 个。

### 7.1 FakeProvider 体系

不发真实网络请求，用三种 FakeProvider：



* **FakeProvider**：按顺序返回预设回复，记录每次调用的输入（验证 \$INPUT 传递）

* **SlowFakeProvider**：有延迟，验证并行执行确实比串行快

* **ErrorProvider**：总是抛异常，验证错误处理和 error 事件

### 7.2 关键测试手法

**手法 1：验证 \$INPUT 传递**



```
\# 第2步调用的输入应包含第1步输出"A"

assert "A" in fake.calls\[1]
```

不直接断言输出，而是断言 "调用 LLM 时传了什么"，更精确。

**手法 2：验证并行确实并行**



```
start = time.time()

results = runner.run\_parallel(specs)  # 3个，每个延迟0.05s

elapsed = time.time() - start

assert elapsed < 0.15  # 串行需0.15s，并行应更快
```

用时间差证明并行，而不是只看结果数量。

**手法 3：验证事件顺序**



```
assert events == \[("start", "w1"), ("done", "w1")]
```

start 必须在 done 之前，且都触发了。

**手法 4：LLM 返回不规范时的容错**

测试 "planner."、"我选择 builder" 等不规范返回，验证 \_match\_agent\_name 能正确匹配。



***

## 八、设计哲学一句话总结

> **没有一种编排模式能解决所有问题 ——Chain 管顺序，Team 管选择，Subagent 管并行；$ORIGINAL 是多步传递的 "锚"，防止意图漂移；无状态是并行的前提。**

三个可迁移到任何项目的通用能力：



1. **多模式编排**：不是所有任务都适合一种模式，根据任务特征选择

2. **意图锚定**：多步处理时保留原始输入，防止信息逐级丢失

3. **无状态并行**：并行执行的单元必须无状态，通过事件而非共享内存通信



***

## 九、面试题

### 基础题

**Q1：Chain、Team、Subagent 三种模式有什么区别？分别适合什么场景？**

> 参考：见第二节对比表。Chain 固定顺序每步都跑，适合有明确步骤的任务；Team 动态选一个子 Agent，适合任务类型不确定需要派活；Subagent 多个并行无状态，适合独立后台任务。

**Q2：****&#x20;****ORIGINAL 分别是什么？为什么需要两个？**

> 参考：
>
> $INPUT 是上一步的输出，保证流水线衔接；$
>
> ORIGINAL 是用户最初的输入，防止多步传递后意图漂移。只有 $INPUT 会导致后续步骤丢失原始需求中的关键约束。

**Q3：什么是无头子 Agent（headless）？为什么并行执行需要无头设计？**

> 参考：无头 = 不直接和用户交互、不维护对话历史、通过事件上报状态。并行时如果每个子 Agent 维护对话历史，会出现状态串台。无状态让并行天然安全。

### 设计题

**Q4：如果 LLM 调度子 Agent 时返回了不存在的名字，你怎么处理？**

> 参考：三层容错匹配（精确→去标点→包含），都匹配不上则明确报错而不是静默 fallback。永远不要假设 LLM 会严格按格式返回。

**Q5：并行执行时，as\_completed 返回顺序不确定，你怎么保证结果和输入顺序对应？**

> 参考：用 dict 按 name 收集结果，最后按输入顺序遍历重排。不要假设完成顺序等于提交顺序。

**Q6：Chain 的 prompt 变量替换为什么用正则而不是 str.replace？**

> 参考：正则的 \b 词边界避免 
>
> $INPUT 匹配到 $
>
> INPUT_2；且如果 
>
> $INPUT 的内容里包含 $
>
> ORIGINAL 字符串，str.replace 会二次替换，正则不会。

### 深入题

**Q7：Team 模式下，如果主 Agent 选错了子 Agent（任务应该给 planner 但选了 builder），怎么兜底？**

> 参考：几种思路 ——①子 Agent 执行后如果发现不匹配，可以返回 "我不适合这个任务"，主 Agent 重新调度；②加一个 fallback 机制，连续选错 N 次后降级为 Chain 或直接让主 Agent 自己处理；③在子 Agent 的 system_prompt 里加 "如果任务不匹配你的职责，明确说明"。

**Q8：Subagent 并行执行时，如果其中一个失败了，其他的还要继续吗？怎么设计？**

> 参考：取决于业务场景。当前实现是独立失败（一个失败不影响其他），结果里标记 success=False。如果需要 "全部成功才算成功"，可以加 all_or_nothing 模式，任一失败则取消其他（但线程取消比较粗暴，更好的方式是用协程 + cancel）。

**Q9：如果 Chain 的某一步输出特别长，导致下一步的 prompt 超过 token 限制，怎么处理？**

> 参考：几种思路 ——①在 Chain 里加中间压缩步骤（复用阶段 6 的 compaction）；②每步输出做摘要而非全文传递；③设置每步输出的最大长度限制；④
>
> $INPUT 只传摘要，$
>
> ORIGINAL 保留完整原始输入。

**Q10：你这个项目的多 Agent 编排和 LangGraph 的子图（subgraph）有什么区别和联系？**

> 参考：LangGraph 子图是把一个图作为另一个图的节点，本质是图的嵌套复用。我们的 Chain/Team/Subagent 是更轻量的编排抽象，不依赖 LangGraph 的图结构 ——Chain 是简单的 for 循环，Team 是两次 LLM 调用，Subagent 是线程池。优势是简单可控、不绑定框架；劣势是没有 LangGraph 的状态管理、检查点、条件边等高级能力。如果未来需要更复杂的编排，可以把 Chain/Team 封装成 LangGraph 节点。

### 场景题

**Q11：让你设计一个 "代码审查助手"，你会用哪种编排模式？怎么设计？**

> 参考：用 Chain。步骤：①解析代码和变更范围 → ②检查代码规范（style）→ ③检查潜在 bug（logic）→ ④检查安全问题（security）→ ⑤汇总生成报告。每步 
>
> $INPUT 是上一步的发现，$
>
> ORIGINAL 是原始代码。也可以用 Subagent 并行跑 style/logic/security 三个检查器，最后汇总 —— 如果检查之间没有依赖关系，并行更快。

**Q12：如果用户说 "帮我订一张去北京的机票，同时查一下北京的天气和酒店"，你会怎么编排？**

> 参考：用 Subagent 并行 —— 机票查询、天气查询、酒店查询是三个独立任务，没有依赖关系，同时跑最快。每个子 Agent 完成后发 done 事件，主 Agent 汇总三个结果返回给用户。



***

*文档生成时间：阶段 8 完成后・全量测试 130 passed*