"""Validated, declarative scene data. Model output is never executable code."""
import math
import re

TYPES = {"question", "dots", "network", "formula", "compare", "flow", "ending", "bars", "curve", "timeline", "geometry", "scattering", "divergence", "lorenz", "amplify", "contrast", "growth", "distinct"}


def distinct_probability(categories, draws):
    """Probability of no repeated category in independent uniform draws."""
    if draws > categories:
        return 0.0
    return math.prod((categories - i) / categories for i in range(draws))


def resolve_visual(scene):
    """Migrate earlier plans that chose a template unable to express their goal."""
    visual = scene.get("visual") if scene.get("visual") in TYPES else "flow"
    spec = scene.get("spec") if isinstance(scene.get("spec"), dict) else {}
    animation = spec.get("animation") if isinstance(spec.get("animation"), dict) else {}
    params = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}
    action = str(animation.get("action") or "")
    hint = " ".join((str(scene.get("heading") or ""), str(spec.get("knowledge_point") or ""), action))
    if visual == "geometry" and any(word in hint for word in ("双翼", "蝴蝶形", "洛伦兹吸引子", "洛伦兹轨迹")):
        return "lorenz"
    if visual == "curve" and not spec.get("elements") and any(word in hint for word in ("分叉", "分离", "发散", "轨迹偏离")):
        return "divergence"
    if visual == "flow" and any(word in hint for word in ("逐级", "放大", "变大", "扩大")):
        return "amplify"
    candidates = params.get("candidates") if isinstance(params.get("candidates"), list) else []
    if visual == "timeline" and len(candidates) >= 2 and not params.get("labels"):
        return "contrast"
    return visual


def number(value, default, low, high):
    try:
        n = float(value)
        return max(low, min(high, n)) if math.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def bounded(value, low, high, integer=False):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        if not math.isfinite(n) or not low <= n <= high or (integer and not n.is_integer()):
            return None
        return int(n) if integer else n
    except (ValueError, TypeError):
        return None


def animation_events(raw):
    events = []
    if not isinstance(raw, list):
        return events
    limits = {"translate_x": (-70, 70), "translate_y": (-45, 45), "scale": (.8, 1.2), "opacity": (.25, 1)}
    for event in raw[:6]:
        if not isinstance(event, dict) or event.get("property") not in limits:
            continue
        low, high = limits[event["property"]]
        start, end = bounded(event.get("start"), 0, 1), bounded(event.get("end"), 0, 1)
        first, last = bounded(event.get("from"), low, high), bounded(event.get("to"), low, high)
        if None in (start, end, first, last) or end <= start:
            continue
        events.append({"property": event["property"], "start": start, "end": end, "from": first, "to": last,
                       "easing": event.get("easing") if event.get("easing") in {"linear", "smooth"} else "smooth",
                       "beat_binding": event.get("beat_binding") is True})
    return events


def relations(raw, count):
    """Validate an undirected edge list; an empty list selects the complete graph."""
    if raw is None or raw == []:
        return [], False
    if not isinstance(raw, list) or not count or len(raw) > 1770:
        return [], True
    result, seen = [], set()
    invalid = False
    for pair in raw[:1770]:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            invalid = True
            continue
        a, b = (bounded(v, 0, count - 1, integer=True) for v in pair)
        if a is None or b is None or a == b:
            invalid = True
            continue
        edge = tuple(sorted((a, b)))
        if edge not in seen:
            seen.add(edge)
            result.append(list(edge))
    return result, invalid


def node_motions(raw, count):
    """Validate one bounded declarative movement per numbered node."""
    if raw is None or raw == []:
        return [], False
    if not isinstance(raw, list) or not count or len(raw) > count:
        return [], True
    result, seen, invalid = [], set(), False
    for item in raw:
        if not isinstance(item, dict):
            invalid = True
            continue
        node = bounded(item.get("node"), 0, count - 1, integer=True)
        times = [bounded(item.get(key), 0, 1) for key in ("start", "end")]
        offsets = [bounded(item.get(key), -90, 90) for key in ("from_x", "from_y", "to_x", "to_y")]
        if (node is None or isinstance(item.get("node"), bool) or node in seen or
                None in times or None in offsets or times[1] <= times[0]):
            invalid = True
            continue
        seen.add(node)
        result.append(dict(zip(("node", "start", "end", "from_x", "from_y", "to_x", "to_y"),
                               [node, *times, *offsets]),
                           easing=item.get("easing") if item.get("easing") in ("linear", "smooth") else "smooth",
                           beat_binding=item.get("beat_binding") is True))
    return result, invalid


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
    count = int(number(p.get("count"), 2, 2, 60)) if p.get("count") not in (None, "") and number(p.get("count"), None, 2, 60) is not None else None
    edges, edges_invalid = relations(p.get("edges"), count)
    edges_invalid = edges_invalid or p.get("edges_invalid") is True
    motions, motions_invalid = node_motions(animation.get("node_motions"), count)
    motions_invalid = motions_invalid or animation.get("node_motions_invalid") is True
    return {
        "version": 1,
        "knowledge_point": str(raw.get("knowledge_point", scene.get("detail", "")))[:160],
        "visual_type": visual,
        "elements": elements,
        "layout": {"safe_margin": 0.06, "composition": "left-title-right-graphic", "graphic_x": number(layout.get("graphic_x"), 650, 520, 750), "graphic_y": number(layout.get("graphic_y"), 255, 200, 300)},
        "animation": {"action": str(animation.get("action", "逐步呈现"))[:80], "motion": motion, "keyframes": [start, finish], "narration_delay": bounded(animation.get("narration_delay"), 0, 2) or 0, "reveal_on_narration": animation.get("reveal_on_narration") is True, "beat_binding": bool(animation.get("beat_binding", True)), "events": animation_events(animation.get("events")), "node_motions": motions, "node_motions_invalid": motions_invalid},
        "transition": raw.get("transition") if raw.get("transition") in {"fade", "cut"} else "fade",
        "style": {"background": "#0b0b0f", "accent": "#d6b36a", "text": "#f5f1e8"},
        "media": {"kind": media.get("kind") if media.get("kind") in {"animation", "image", "video"} else "animation", "prompt": str(media.get("prompt") or "")[:1000], "asset_id": asset_id, "request_id": request_id, "submission_state": media.get("submission_state") if media.get("submission_state") in {"uncertain", "submitted"} else ""},
        "parameters": {"categories": bounded(p.get("categories"), 2, 1000000, integer=True), "draws": bounded(p.get("draws"), 1, 60, integer=True), "optical_path": p.get("optical_path") if p.get("optical_path") in ("short", "long") else "short", "principal": bounded(p.get("principal"), .01, 1000000000), "rate": bounded(p.get("rate"), 0, 100), "periods": bounded(p.get("periods"), 1, 30, integer=True), "count": count, "edges": edges, "edges_invalid": edges_invalid, "sides": int(number(p.get("sides"), 3, 3, 8)), "value": number(p.get("value"), None, 0, 100) if p.get("value") not in (None, "") else None, "secondary_value": number(p.get("secondary_value"), None, 0, 100) if p.get("secondary_value") not in (None, "") else None, "formula": str(p.get("formula", scene.get("detail", "")))[:100], "labels": [str(x)[:24] for x in p.get("labels", [])[:8]] if isinstance(p.get("labels"), list) else [], "candidates": [str(x)[:20] for x in p.get("candidates", [])[:5]] if isinstance(p.get("candidates"), list) else []},
        "assets": [], "timing": {"start": None, "end": None},
    }
