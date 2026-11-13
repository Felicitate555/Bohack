import json
from ..providers import siliconflow
from .creative_brief import PRODUCTION_PRINCIPLES, RENDERER_CONTRACT


def improve(scene, instruction):
    system = PRODUCTION_PRINCIPLES + "\n" + RENDERER_CONTRACT + """
你负责按用户要求修改一个现有镜头，输出完整的 JSON 镜头对象，而不是项目或场景数组。优先保留原有准确的知识点、口播和数值，仅在用户要求或确有错误时修改；不得丢失组件必需的数据。让画面表达知识本身，避免只把英文内部标签放大。若要求当前组件无法实现，就选最接近且不误导的组件，不能用 action 文字假装已经实现。
输出字段必须包括 heading、narration、key、detail、visual 和 spec。所有输出仅作声明式数据，不得包含代码。"""
    content, event = siliconflow.chat("animation", [{"role": "system", "content": system}, {"role": "user", "content": json.dumps({"scene": scene, "request": instruction}, ensure_ascii=False)}], max_tokens=2500)
    return siliconflow.parse_json(content), event


def improve_plan(topic, scenes):
    system = PRODUCTION_PRINCIPLES + "\n" + RENDERER_CONTRACT + """
你是全片动态图形导演。收到已经完成的场景列表后，在已有组件范围内提高逐镜解释力和整片画面变化。保持镜头数量和顺序，不要改动标题、旁白、已有准确结论或捏造数值；画面参数必须与口播的实际含义一致。
让相邻镜头尽可能使用不同但合适的表达，关键过程使用图形逐步建立，关键数字选择可解释其含义的画面，不要所有镜头都变成大字或流程线。能用素材作为背景增强真实氛围时可选择 image/video 并提供英文 prompt；公式和精确图表优先使用 animation。单个组件无法实现的复杂动作不可伪装成已实现。
只输出 JSON 对象 {"scenes":[完整镜头对象,...]}；每一项必须包含 heading、narration、key、detail、visual 和 spec，不包含代码。"""
    source = json.dumps({"topic": topic, "scenes": scenes}, ensure_ascii=False)
    content, event = siliconflow.chat("animation", [{"role": "system", "content": system}, {"role": "user", "content": source}], max_tokens=10000)
    return siliconflow.parse_json(content), event
