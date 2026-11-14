import json
import os
import re
from pathlib import Path

import requests
from .scene_spec import scene_spec, resolve_visual, TYPES
from .providers import siliconflow
from .agents import knowledge_writer, animation_builder, emotion_director
from .agents.creative_brief import DIRECTOR_INSTRUCTIONS
from .services import media_router
from . import style_profile

ROOT = Path(__file__).resolve().parent.parent
VISUALS = TYPES


def example():
    return json.loads((ROOT / "examples" / "birthday.json").read_text(encoding="utf-8"))


def generate(topic: str, target_seconds=None, mode="smart", progress=lambda message: None, checkpoint=lambda plan, events: None, initial_script=None, profile=None):
    legacy_key = os.getenv("LLM_API_KEY", "").strip()
    legacy_model = os.getenv("LLM_MODEL", "").strip()
    if not siliconflow.api_key() and not (legacy_key and legacy_model):
        raise ValueError("请先在 .env 中配置 SILICONFLOW_API_KEY，或选择零密钥示例。")
    system = DIRECTOR_INSTRUCTIONS + "\n" + style_profile.director_context(profile)
    if target_seconds:
        system += f"\n用户选择的目标成片约 {target_seconds} 秒。写与该时长相称的有效口播，不要通过重复或空话凑时长。"
        if target_seconds >= 90:
            system += "\n较长影片建议规划10至12个各自承担不同解释任务的镜头，以便画面有变化；不要为了凑数量重复内容。"
        elif target_seconds <= 30:
            system += "\n短片建议规划4至6个镜头。"
    try:
        records = []
        if not siliconflow.api_key():
            response = requests.post(os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions", headers={"Authorization":"Bearer " + legacy_key}, json={"model":legacy_model,"messages":[{"role":"system","content":system},{"role":"user","content":topic}]}, timeout=90)
            if not response.ok:
                raise ValueError(f"原模型接口返回 HTTP {response.status_code}，请检查配置。")
            body = response.json()
            obj = siliconflow.parse_json(body["choices"][0]["message"]["content"])
            obj["topic"] = topic
            obj["style_profile"] = profile
            result = validate(obj)
            result["emotion_plan"] = emotion_director.build(obj.get("emotion_plan"), result["scenes"])
            result["warnings"] = [result["emotion_plan"]["message"]] if result["emotion_plan"]["status"] == "fallback" else []
            return result, [{"role":"legacy", "model":legacy_model, "usage":body.get("usage", {})}]
        if mode == "fast":
            source = topic
        else:
            if isinstance(initial_script, dict):
                script = initial_script
                progress("正在复用已保存的编剧草稿")
            else:
                progress("正在编写内容和解释逻辑")
                try:
                    script, event = knowledge_writer.write(topic, target_seconds, profile=profile)
                except ValueError as exc:
                    raise ValueError(f"编剧阶段失败：{exc}") from exc
                records.append(event)
                checkpoint({"title": str(script.get("title") or topic)[:80], "scenes": [], "knowledge_script": script}, records)
            source = "主题：" + topic + "\n编剧草稿（对不确定内容继续标注）：" + json.dumps(script, ensure_ascii=False)[:16000]
        role = "director_pro" if mode == "director" else ("writer" if mode == "fast" else "director")
        progress("正在规划分镜与视觉表达" if mode != "director" else "旗舰导演正在规划分镜与视觉表达")
        try:
            raw, event = siliconflow.chat(role, [{"role": "system", "content": system}, {"role": "user", "content": source}], max_tokens=12000 if mode == "director" else 6000)
        except ValueError as exc:
            label = "旗舰导演" if mode == "director" else "视觉导演"
            raise ValueError(f"{label}阶段失败：{exc}") from exc
        records.append(event)
        try:
            obj = siliconflow.parse_json(raw)
            obj["topic"] = topic
            obj["style_profile"] = profile
            result = validate(obj)
        except ValueError as exc:
            raise ValueError(f"{'旗舰导演' if mode == 'director' else '视觉导演'}返回的分镜不可用：{exc}") from exc
        issues = visual_issues(result)
        if issues:
            progress("正在修正无法绘制的镜头")
            repair = "生成的方案存在这些可执行性问题：" + "；".join(issues) + "。请重新输出完整项目 JSON。无法确认数据时换成合适的现有组件，不要编造数据。"
            raw, event = siliconflow.chat(role, [
                {"role": "system", "content": system},
                {"role": "user", "content": source},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": repair},
            ], max_tokens=12000 if mode == "director" else 6000)
            records.append(event)
            obj = siliconflow.parse_json(raw)
            obj["topic"] = topic
            obj["style_profile"] = profile
            result = validate(obj)
            issues = visual_issues(result)
            if issues:
                raise ValueError("生成方案仍无法绘制：" + "；".join(issues) + "。请重试或更换模型。")
        warnings = []
        emotion_raw = obj.get("emotion_plan")
        result["emotion_plan"] = emotion_director.build(emotion_raw, result["scenes"])
        if result["emotion_plan"]["status"] == "fallback":
            warnings.append(result["emotion_plan"]["message"])
        checkpoint(result, records)
        if mode == "director":
            try:
                progress("正在优化动画设计；基础分镜已保存")
                refined, event = animation_builder.improve_plan(topic, result["scenes"], profile=profile)
                records.append(event)
                candidate = validate({"title": result["title"], "topic": topic, "scenes": refined.get("scenes"), "style_profile": profile})
                if len(candidate["scenes"]) != len(result["scenes"]) or visual_issues(candidate):
                    raise ValueError("动画规划的镜头数量或图表参数无效")
                for old, new in zip(result["scenes"], candidate["scenes"]):
                    new["heading"], new["narration"] = old["heading"], old["narration"]
                result["scenes"] = candidate["scenes"]
                result["emotion_plan"] = emotion_director.build(emotion_raw, result["scenes"])
            except (ValueError, TypeError, KeyError) as exc:
                warnings.append(f"动画导演优化未完成，已保留旗舰导演分镜：{exc}")
        for scene in result["scenes"]:
            scene["spec"]["media"]["kind"] = media_router.choose(scene, mode)
        result["warnings"] = warnings
        result["knowledge_script"] = script if mode != "fast" else None
        return result, records
    except requests.RequestException as exc:
        raise ValueError("无法连接原模型服务，请检查网络。") from exc
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("模型返回的分镜格式不正确，请重试或更换模型。") from exc


def validate(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("scenes"), list) or not 1 <= len(obj["scenes"]) <= 16:
        raise ValueError("分镜应为 1 至 16 个镜头。")
    title = str(obj.get("title", "未命名作品"))[:80]
    topic = str(obj.get("topic", ""))[:500]
    profile = style_profile.normalize(obj.get("style_profile"))
    palette = profile["palette"]
    scenes = []
    for s in obj["scenes"]:
        if not isinstance(s, dict):
            raise ValueError("分镜格式错误。")
        scene = {k: str(s.get(k, "")).strip()[:limit] for k, limit in {"heading": 40, "narration": 260, "key": 24, "detail": 80}.items()}
        if not scene["heading"] or not scene["narration"]:
            raise ValueError("每个镜头都需要标题和旁白。")
        # Internal labels such as q_world or amplify should never become the
        # biggest words in a Chinese-language finished video.
        if re.search(r"[\u4e00-\u9fff]", scene["heading"]) and re.fullmatch(r"[a-z][a-z0-9_]{2,}", scene["key"]):
            scene["key"] = scene["heading"][:20]
        scene["visual"] = resolve_visual(s)
        scene["spec"] = scene_spec({**s, **scene})
        scene["spec"]["style"] = {key: palette[key] for key in ("background", "accent", "text")}
        raw_spec = s.get("spec") if isinstance(s.get("spec"), dict) else {}
        raw_animation = raw_spec.get("animation") if isinstance(raw_spec.get("animation"), dict) else {}
        if raw_spec.get("transition") not in ("fade", "cut"):
            scene["spec"]["transition"] = profile["motion"]["default_transition"]
        if "beat_binding" not in raw_animation:
            scene["spec"]["animation"]["beat_binding"] = profile["motion"]["beat_emphasis"]
        scenes.append(scene)
    return {"title": title, "topic": topic, "scenes": scenes}


def visual_issues(plan):
    issues = []
    for i, scene in enumerate(plan["scenes"], 1):
        spec = scene["spec"]
        if scene["visual"] in {"bars", "curve"} and (len(spec.get("elements", [])) < (2 if scene["visual"] == "curve" else 1) or any(e["value"] < 0 for e in spec.get("elements", []))):
            issues.append(f"镜头 {i} 的图表缺少足够的非负数据")
        if scene["visual"] == "compare" and spec.get("parameters", {}).get("value") is None:
            issues.append(f"镜头 {i} 的比例图缺少百分比")
        if scene["visual"] == "growth" and any(spec.get("parameters", {}).get(k) is None for k in ("principal", "rate", "periods")):
            issues.append(f"镜头 {i} 的复利动画需要有效本金、每期增长率和整数期数")
        if scene["visual"] == "distinct":
            params = spec.get("parameters", {})
            categories, draws = params.get("categories"), params.get("draws")
            if categories is None or draws is None or draws > categories + 1:
                issues.append(f"镜头 {i} 的不重复概率需要有效类别数和抽取次数（最多类别数加一）")
        if scene["visual"] == "network" and spec.get("parameters", {}).get("edges_invalid"):
            issues.append(f"镜头 {i} 的关系网络含无效节点或连线")
        if scene["visual"] in {"network", "dots"} and spec.get("animation", {}).get("node_motions_invalid"):
            issues.append(f"镜头 {i} 的节点运动含无效编号、范围或重复节点")
    return issues
