"""Local, declarative visual direction; no reference media is embedded."""
import copy
import json
import math
import re


DEFAULT = {
    "id": "black-gold-documentary",
    "name": "黑金知识纪录片",
    "palette": {"background": "#08090b", "surface": "#101010", "glow": "#242015",
                "accent": "#d6b36a", "text": "#f5f1e8", "muted": "#9d927c"},
    "typography": {"family": "serif", "number_family": "serif", "title_size": 30, "subtitle_size": 21},
    "layout": {"subtitle_y": 470, "safe_margin": .05},
    "effects": {"grain": .10, "vignette": .36, "media_opacity": .60, "media_overlay": .25, "camera_push": .015},
    "motion": {"default_transition": "fade", "beat_emphasis": True},
    "direction": "克制黑金、暖白文字、哑光金属、细线与环境光；镜头逐步建立关系，留白清楚，字幕避开主体。",
    "media_prompt": "restrained black and champagne gold documentary aesthetic, matte metal, low-key cinematic light, subtle dust and grain, clear space for graphics, no text, no watermark",
    "sound_direction": "自然中文知识纪录片叙述；音乐让位于旁白，节奏跟随内容，不强行卡点。",
}


def normalize(raw=None):
    """Return a bounded profile, merging old or incomplete projects safely."""
    result = copy.deepcopy(DEFAULT)
    if not isinstance(raw, dict):
        return result
    for key, limit in (("id", 80), ("name", 80), ("direction", 400), ("media_prompt", 500), ("sound_direction", 300)):
        if isinstance(raw.get(key), str) and raw[key].strip():
            result[key] = raw[key].strip()[:limit]
    palette = raw.get("palette") if isinstance(raw.get("palette"), dict) else {}
    for key in result["palette"]:
        value = palette.get(key)
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            result["palette"][key] = value.lower()
    typography = raw.get("typography") if isinstance(raw.get("typography"), dict) else {}
    for key in ("family", "number_family"):
        if typography.get(key) in ("serif", "sans"):
            result["typography"][key] = typography[key]
    motion = raw.get("motion") if isinstance(raw.get("motion"), dict) else {}
    if motion.get("default_transition") in ("fade", "cut"):
        result["motion"]["default_transition"] = motion["default_transition"]
    if isinstance(motion.get("beat_emphasis"), bool):
        result["motion"]["beat_emphasis"] = motion["beat_emphasis"]
    for group, limits in (("typography", {"title_size": (22, 42), "subtitle_size": (16, 28)}),
                          ("layout", {"subtitle_y": (440, 485), "safe_margin": (.04, .07)}),
                          ("effects", {"grain": (0, .2), "vignette": (0, .8), "media_opacity": (.2, .9), "media_overlay": (0, .65), "camera_push": (0, .05)})):
        incoming = raw.get(group) if isinstance(raw.get(group), dict) else {}
        for key, (lo, hi) in limits.items():
            value = incoming.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and lo <= value <= hi:
                result[group][key] = value
    return result


def director_context(raw=None):
    profile = normalize(raw)
    return "当前风格配置（作为创作方向，不改变事实）：" + json.dumps(profile, ensure_ascii=False, separators=(",", ":"))


def media_prompt(prompt, raw=None):
    return str(prompt).strip() + ". Visual style: " + normalize(raw)["media_prompt"]
