import json
from ..providers import siliconflow


def improve(scene, instruction):
    system = "你是动态图形设计师。根据要求修改一个镜头，仅返回JSON镜头对象。禁止生成代码、禁止虚构数据。保留heading、narration、key、detail，visual仅能是question,dots,network,formula,compare,flow,bars,curve,timeline,geometry,scattering,ending。spec可包含knowledge_point、parameters（count、value、secondary_value、formula、labels、candidates、sides）、elements、layout（graphic_x、graphic_y）、animation（action、motion为smooth/linear/step，keyframes为0到1起止比例）。输出必须为可执行组件的声明式数据。"
    content, event = siliconflow.chat("animation", [{"role":"system","content":system}, {"role":"user","content":json.dumps({"scene":scene,"request":instruction}, ensure_ascii=False)}], max_tokens=2200)
    return siliconflow.parse_json(content), event
