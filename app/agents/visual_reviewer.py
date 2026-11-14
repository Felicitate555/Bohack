import json
from ..providers import siliconflow
from .. import style_profile


def review(scene, frames, profile=None, emotion_beats=None):
    text = """你是短片视觉审片师，比较给出的实际关键帧与镜头计划，而不是只评价文字描述。按优先级检查：画面是否真正解释核心知识、数字和标签是否互相矛盾、是否出现缺数据提示、内部变量名或重复的泛泛免责声明、字幕与公式是否被遮挡或过小、素材主体是否可见、信息密度与当前风格是否一致。每条问题应说明画面证据及可执行的改进建议，不要泛泛夸奖。
你只看到若干静帧，不能据此断言音乐节拍、旁白同步、完整运动流畅性、所有字幕均可读、事实与数据已被验证；看不出来的应明确标为无法判断。只输出 JSON 对象：summary，issues 数组（每项 severity 为 high/medium/low，problem，suggestion），needs_revision 布尔值。镜头计划：""" + json.dumps(scene, ensure_ascii=False)[:6000]
    if emotion_beats:
        text += "\n当前镜头的情绪设计意图（不是观众心理检测）：" + json.dumps(emotion_beats, ensure_ascii=False)[:2500]
        text += "\n根据可见画面检查问题、候选、答案和解释是否与意图冲突；静帧不能证明期待、节奏或音效已经实现。"
    text += "\n" + style_profile.director_context(profile)
    content = [{"type": "text", "text": text}]
    content += [{"type": "image_url", "image_url": {"url": siliconflow.image_data_url(frame)}} for frame in frames]
    result, usage = siliconflow.chat("reviewer", [{"role": "user", "content": content}], max_tokens=1800)
    return siliconflow.parse_json(result), usage
