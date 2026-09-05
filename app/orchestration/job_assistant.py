"""阶段8 · 课4：求职助手（主线落地）。

用 AgentChain 串起三步流水线：
1. planner：分析 JD，提取核心要求（技能/经验/职责）
2. builder：匹配求职者技能，生成求职建议（简历优化/面试准备）
3. reviewer：复核建议质量，给出改进意见

$ORIGINAL 全程保留 JD + 求职者技能，防止多步后丢失原始信息。
"""

from dataclasses import dataclass

from app.orchestration.chain import AgentChain, ChainStep, ChainResult
from app.providers.base import LLMProvider


# 三步流水线的 prompt 模板
PLANNER_PROMPT = """你是资深招聘专家。请分析以下职位描述（JD），提取核心要求。

职位描述：
$ORIGINAL

请按以下格式输出：
1. 必备技能（3-5项）
2. 优先技能（2-3项）
3. 核心职责（2-3项）
4. 经验要求
"""

BUILDER_PROMPT = """你是职业规划师。基于以下 JD 分析结果，结合求职者技能，生成求职建议。

JD 分析结果：
$INPUT

原始信息（JD + 求职者技能）：
$ORIGINAL

请按以下格式输出：
1. 技能匹配度分析（已具备/需补充）
2. 简历优化建议（3条）
3. 面试准备重点（3条）
4. 学习提升建议（2条）
"""

REVIEWER_PROMPT = """你是严格的质量审核员。请复核以下求职建议的质量。

求职建议：
$INPUT

原始信息（JD + 求职者技能）：
$ORIGINAL

请按以下格式输出：
1. 建议质量评分（1-10分）及理由
2. 遗漏的关键点（如有）
3. 需要改进的地方（2-3条）
4. 最终优化后的建议摘要
"""


@dataclass
class JobAssistantInput:
    """求职助手的输入。"""
    jd: str           # 职位描述
    skills: str = ""  # 求职者技能（可选）


class JobAssistant:
    """求职助手：planner → builder → reviewer 三步流水线。"""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.chain = AgentChain(
            steps=[
                ChainStep(name="planner", prompt=PLANNER_PROMPT),
                ChainStep(name="builder", prompt=BUILDER_PROMPT),
                ChainStep(name="reviewer", prompt=REVIEWER_PROMPT),
            ],
            provider=provider,
        )

    def run(self, jd: str, skills: str = "") -> ChainResult:
        """执行求职助手流水线。

        Args:
            jd: 职位描述
            skills: 求职者技能（可选）

        Returns:
            ChainResult：含最终输出（reviewer 的复核结果）和每步中间结果
        """
        original = f"【职位描述】\n{jd}"
        if skills:
            original += f"\n\n【求职者技能】\n{skills}"
        return self.chain.run(original)
