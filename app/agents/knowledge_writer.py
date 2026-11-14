from ..providers import siliconflow
from .creative_brief import PRODUCTION_PRINCIPLES
from .. import style_profile


def write(topic, duration=None, profile=None):
    instruction = PRODUCTION_PRINCIPLES + """
你的职责是编剧与内容策划，给后续导演提供可用的创作蓝图；不输出代码。
先提取并遵守用户明确提供的受众、结构、事实、示例、脚本和视觉要求；原文有多个段落时，确保后续导演能看见各段核心内容与先后关系。仅当用户没有提供时，才自行补充创意；时间戳是创作意图而非已测量的配音或音乐节拍。
每段 visual_idea 说明可见主体、它如何随讲解变化以及想帮助观众理解什么；需要的数据须有来源于用户或明确标注为假设。不要把泛泛的“高级感”“动态演示”当作视觉方案，不要声称未知参考视频已被理解。
先明确受众和需要记住的结论，设计有吸引力但不夸大事实的开场；把复杂内容拆成循序渐进的解释，指出常见误解、必要前提、直观例子、关键数值和最终收束。故事类主题则根据其内容设计人物、冲突和变化，不要伪装成知识课程。
对于计算或数据，给出条件、单位、计算过程和必要的近似说明。比如生日问题应说明一年按365天、生日均匀且相互独立等简化假设；这个例子只在相关题材适用。无法确定的事实放在 uncertainties 中并避免纳入确定性口播。
叙述应适合口语和字幕：短句、自然转折、一个句子一个重点；尽量让每一段都有不同的信息功能。结合时长写足有效内容，不为凑时长反复讲同一件事；长作品预留开场与结尾时间。给每一段建议解释目标和可视化思路，但不要假装图形已被制作、音乐已被分析或数据已被外部验证。
在现有编剧调用中兼任情绪设计：依据事实和受众规划期待、变化与知识回报，避免夸张、虚构或固定反转套路。加入 emotion_direction 对象，包含 audience_expectation、core_tension、payoff 和 arc；说明应让观众看到和听到什么，不推断真实观众心理。
只输出 JSON 对象，字段包括 title、audience、hook、emotion_direction、learning_goals（数组）、key_facts（数组，说明依据和假设）、uncertainties（数组）、narrative（数组，每项含 purpose、narration、visual_idea）、narration（整段口播）。不要引用没有查证的文献或编造来源。
"""
    if duration:
        instruction += f"\n目标成片约 {duration} 秒，叙述量应与时长相协调。"
    instruction += "\n" + style_profile.director_context(profile)
    text, usage = siliconflow.chat("writer", [{"role": "system", "content": instruction}, {"role": "user", "content": topic}], max_tokens=3500)
    return siliconflow.parse_json(text), usage
