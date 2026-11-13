"""Validated, declarative scene data. Model output is never executable code."""
import math
import re

TYPES = {"question", "dots", "network", "formula", "compare", "flow", "ending", "bars", "curve", "timeline", "geometry", "scattering"}


def number(value, default, low, high):
    try:
        n = float(value)
        return max(low, min(high, n)) if math.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def scene_spec(scene):
    raw = scene.get("spec") if isinstance(scene.get("spec"), dict) else {}
    visual = scene.get("visual") if scene.get("visual") in TYPES else "flow"
    p = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    animation = raw.get("animation") if isinstance(raw.get("animation"), dict) else {}
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    asset_id = str(media.get("asset_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", asset_id):
        asset_id = ""
    request_id = str(media.get("request_id") or "")[:100]
    motion = animation.get("motion") if animation.get("motion") in {"smooth", "linear", "step"} else "smooth"
    keyframes = animation.get("keyframes") if isinstance(animation.get("keyframes"), list) else []
    start = number(keyframes[0], 0, 0, .8) if len(keyframes) > 0 else 0
    finish = number(keyframes[1], .65, .1, 1) if len(keyframes) > 1 else .65
    if finish <= start:
        finish = min(1, start + .2)
    elements = raw.get("elements") if isinstance(raw.get("elements"), list) else []
    elements = [{"label": str(e.get("label", ""))[:24], "value": number(e.get("value"), None, -10000, 10000)} for e in elements[:8] if isinstance(e, dict)]
    elements = [e for e in elements if e["value"] is not None]
    return {
        "version": 1,
        "knowledge_point": str(raw.get("knowledge_point", scene.get("detail", "")))[:160],
        "visual_type": visual,
        "elements": elements,
        "layout": {"safe_margin": 0.06, "composition": "left-title-right-graphic", "graphic_x": number(layout.get("graphic_x"), 650, 520, 750), "graphic_y": number(layout.get("graphic_y"), 255, 200, 300)},
        "animation": {"action": str(animation.get("action", "逐步呈现"))[:80], "motion": motion, "keyframes": [start, finish], "beat_binding": bool(animation.get("beat_binding", True))},
        "transition": raw.get("transition") if raw.get("transition") in {"fade", "cut"} else "fade",
        "style": {"background": "#0b0b0f", "accent": "#d6b36a", "text": "#f5f1e8"},
        "media": {"kind": media.get("kind") if media.get("kind") in {"animation", "image", "video"} else "animation", "prompt": str(media.get("prompt") or "")[:1000], "asset_id": asset_id, "request_id": request_id, "submission_state": media.get("submission_state") if media.get("submission_state") in {"uncertain", "submitted"} else ""},
        "parameters": {"count": int(number(p.get("count"), 2, 2, 60)) if p.get("count") not in (None, "") and number(p.get("count"), None, 2, 60) is not None else None, "sides": int(number(p.get("sides"), 3, 3, 8)), "value": number(p.get("value"), None, 0, 100) if p.get("value") not in (None, "") else None, "secondary_value": number(p.get("secondary_value"), None, 0, 100) if p.get("secondary_value") not in (None, "") else None, "formula": str(p.get("formula", scene.get("detail", "")))[:100], "labels": [str(x)[:24] for x in p.get("labels", [])[:8]] if isinstance(p.get("labels"), list) else [], "candidates": [str(x)[:20] for x in p.get("candidates", [])[:5]] if isinstance(p.get("candidates"), list) else []},
        "assets": [], "timing": {"start": None, "end": None},
    }
