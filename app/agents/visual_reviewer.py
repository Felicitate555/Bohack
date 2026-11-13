import json
from ..providers import siliconflow


def review(scene, frames):
    text = "请审查知识动画的代表性关键帧。只输出JSON对象，包含summary、issues（数组：severity、problem、suggestion）、needs_revision。检查是否表达知识、文字遮挡和风格一致。仅凭静帧不能判断动画连贯性或验证科学数值。镜头计划：" + json.dumps(scene, ensure_ascii=False)[:6000]
    content = [{"type": "text", "text": text}]
    content += [{"type": "image_url", "image_url": {"url": siliconflow.image_data_url(frame)}} for frame in frames]
    result, usage = siliconflow.chat("reviewer", [{"role": "user", "content": content}], max_tokens=1400)
    return siliconflow.parse_json(result), usage
