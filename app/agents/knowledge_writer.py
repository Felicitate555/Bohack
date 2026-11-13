from ..providers import siliconflow


def write(topic, duration=None):
    instruction = "你是知识短视频编剧。只输出JSON对象，包含title、hook、learning_goals、key_facts、uncertainties、narrative和narration。解释真实准确，未经核实的事实标为不确定，数学数字不得凭空编造；语言自然，有悬念、有解释、有收束。不要编造来源。"
    if duration:
        instruction += f"目标约{duration}秒，控制篇幅。"
    text, usage = siliconflow.chat("writer", [{"role": "system", "content": instruction}, {"role": "user", "content": topic}])
    data = siliconflow.parse_json(text)
    return data, usage
