import json
import os
import re
from pathlib import Path

import requests
from .scene_spec import scene_spec, TYPES
from .providers import siliconflow
from .agents import knowledge_writer
from .services import media_router

ROOT = Path(__file__).resolve().parent.parent
VISUALS = TYPES


def example():
    return json.loads((ROOT / "examples" / "birthday.json").read_text(encoding="utf-8"))


def generate(topic: str, target_seconds=None, mode="smart"):
    legacy_key = os.getenv("LLM_API_KEY", "").strip()
    legacy_model = os.getenv("LLM_MODEL", "").strip()
    if not siliconflow.api_key() and not (legacy_key and legacy_model):
        raise ValueError("请先在 .env 中配置 SILICONFLOW_API_KEY，或选择零密钥示例。")
    system = """你是短片编剧兼知识动态图形导演。把主题组织为4至6个有因果的分镜，只输出JSON。每镜头用1至2句口语旁白解释，不编造事实。visual只可用question悬念数字、dots点阵、network两两关系、formula公式逐项呈现、compare百分比对比、flow节点流程、bars柱状图、curve变化曲线、timeline时间线、geometry几何、ending总结。根据含义选择，每镜头都填写spec参数，不能只是替换模板文字：count代表实际点或节点数（2至60）；value与secondary_value为真实的百分数（0至100）；formula为真实算式；labels为简短标签；elements是可视化标签和数值数组。不确定具体值就不要编数字。heading少于14汉字，key少于12字符，detail简短。网络可表达两两连线；图形是示意而非真实影片。格式：{"title":"标题","scenes":[{"heading":"","narration":"","key":"","detail":"","visual":"network","spec":{"knowledge_point":"解释目的","parameters":{"count":23,"value":50.73,"secondary_value":49.27,"formula":"1−49.27%=50.73%","labels":["全部不同","至少一对相同"]},"elements":[{"label":"A","value":30}],"animation":{"action":"逐步连线"}}}]}"""
    system += "\n视觉类型还支持scattering（光线遇到大气分子后向各方向散射的示意，适用于天空为什么蓝）。每镜头还可给layout.graphic_x（520到750）、graphic_y（200到300）安排图形；animation.motion为smooth、linear或step，keyframes是0到1的起止比例，beat_binding控制是否在实际节拍上强调；transition为fade或cut；悬念question的parameters.candidates是依次揭示的短词数组。避免在其他主题照搬示例中的23和概率。"
    if target_seconds:
        system += f"\n目标成片约{target_seconds}秒，根据目标控制口播长度，避免为了凑时长重复叙述。"
    system += "\n每镜头 spec.media.kind 可选 animation、image、video；spec.media.prompt 是不含文字或公式的英语画面描述。数学和需要准确呈现的镜头必须选 animation；只有自然景观、真实运动和抽象氛围才考虑 image/video。不要把所有镜头都选素材。"
    try:
        records = []
        if not siliconflow.api_key():
            response = requests.post(os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions", headers={"Authorization":"Bearer " + legacy_key}, json={"model":legacy_model,"messages":[{"role":"system","content":system},{"role":"user","content":topic}]}, timeout=90)
            if not response.ok:
                raise ValueError(f"原模型接口返回 HTTP {response.status_code}，请检查配置。")
            body = response.json()
            obj = siliconflow.parse_json(body["choices"][0]["message"]["content"])
            obj["topic"] = topic
            result = validate(obj)
            return result, [{"role":"legacy", "model":legacy_model, "usage":body.get("usage", {})}]
        if mode == "fast":
            source = topic
        else:
            script, event = knowledge_writer.write(topic, target_seconds)
            records.append(event)
            source = "主题：" + topic + "\n编剧草稿（对不确定内容继续标注）：" + json.dumps(script, ensure_ascii=False)[:16000]
        role = "director_pro" if mode == "director" else ("writer" if mode == "fast" else "director")
        raw, event = siliconflow.chat(role, [{"role": "system", "content": system}, {"role": "user", "content": source}])
        records.append(event)
        obj = siliconflow.parse_json(raw)
        obj["topic"] = topic
        result = validate(obj)
        for scene in result["scenes"]:
            scene["spec"]["media"]["kind"] = media_router.choose(scene, mode)
        result["knowledge_script"] = script if mode != "fast" else None
        return result, records
    except requests.RequestException as exc:
        raise ValueError("无法连接原模型服务，请检查网络。") from exc
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("模型返回的分镜格式不正确，请重试或更换模型。") from exc


def validate(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("scenes"), list) or not 1 <= len(obj["scenes"]) <= 8:
        raise ValueError("分镜应为 1 至 8 个镜头。")
    title = str(obj.get("title", "未命名作品"))[:80]
    topic = str(obj.get("topic", ""))[:500]
    scenes = []
    for s in obj["scenes"]:
        if not isinstance(s, dict):
            raise ValueError("分镜格式错误。")
        scene = {k: str(s.get(k, "")).strip()[:limit] for k, limit in {"heading": 40, "narration": 260, "key": 24, "detail": 80}.items()}
        if not scene["heading"] or not scene["narration"]:
            raise ValueError("每个镜头都需要标题和旁白。")
        scene["visual"] = s.get("visual") if s.get("visual") in VISUALS else "flow"
        scene["spec"] = scene_spec({**s, **scene})
        scenes.append(scene)
    return {"title": title, "topic": topic, "scenes": scenes}
