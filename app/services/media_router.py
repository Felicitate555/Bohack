"""Choose scarce media only when they contribute to the explanation."""
MATHEMATICAL = ("概率", "公式", "数字", "计算", "比例", "推导", "图表", "配对", "变量", "函数", "算法", "逻辑")


def choose(scene, mode):
    suggested = (scene.get("spec", {}).get("media") or {}).get("kind", "animation")
    text = " ".join(str(scene.get(k, "")) for k in ("heading", "narration", "detail"))
    if mode == "fast" or scene.get("visual") in {"formula", "network", "compare", "bars", "curve"} or any(w in text for w in MATHEMATICAL):
        return "animation"
    if mode == "smart" and suggested == "video":
        return "image"
    return suggested if suggested in {"image", "video"} else "animation"
