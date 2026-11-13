import os
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from PIL import Image
from ..providers import siliconflow
from ..providers.model_registry import model


def price(name):
    try:
        n = Decimal(os.getenv(name, ""))
        return n if n.is_finite() and n > 0 else None
    except InvalidOperation:
        return None


def prepare(plan, directory, budget_cny, update, persist):
    """Generate bounded optional assets; never turn an unknown price into zero."""
    if plan.get("creative_mode") == "fast":
        return
    max_images = 1
    max_videos = 1 if plan.get("creative_mode") == "director" else 0
    image_count = video_count = 0
    remaining = Decimal(str(budget_cny))
    asset_dir = directory / "assets"
    asset_dir.mkdir(exist_ok=True)
    for idx, scene in enumerate(plan["scenes"]):
        media = scene["spec"]["media"]
        kind = media.get("kind")
        if kind not in {"image", "video"} or not media.get("prompt"):
            continue
        if media.get("asset_id") and (asset_dir / (media["asset_id"] + (".mp4" if kind == "video" else ".png"))).exists():
            continue
        if media.get("submission_state") == "uncertain" and not media.get("request_id"):
            plan.setdefault("warnings", []).append(f"镜头 {idx+1} 上一次素材请求结果不明确，已停止重复提交；请检查平台账单与任务。")
            persist(); continue
        if kind == "image" and image_count >= max_images or kind == "video" and video_count >= max_videos:
            continue
        unit = price("SF_PRICE_IMAGE_CNY" if kind == "image" else "SF_PRICE_VIDEO_SECOND_CNY")
        estimate = unit * (5 if kind == "video" else 1) if unit else None
        if not media.get("request_id") and (not estimate or estimate > remaining):
            plan.setdefault("warnings", []).append(f"镜头 {idx+1} 缺少价格配置或预算不足，使用程序化动画。")
            persist(); continue
        if kind == "image": image_count += 1
        else: video_count += 1
        asset_id = uuid.uuid4().hex
        event = None
        target = asset_dir / (asset_id + (".mp4" if kind == "video" else ".png"))
        update(f"正在制作镜头 {idx+1} 的{'图片' if kind == 'image' else '视频'}素材")
        try:
            if kind == "image":
                remaining -= estimate
                event = {"role": "image", "model": model("image"), "estimated_cny": str(estimate), "actual_cny": None, "status": "submitted"}
                media["submission_state"] = "uncertain"
                plan.setdefault("usage_events", []).append(event); persist()
                url = siliconflow.image(media["prompt"])
                siliconflow.download(url, target, 30*1024*1024)
                with Image.open(target) as img:
                    if img.width * img.height > 20_000_000:
                        raise ValueError("图片尺寸过大")
                    img.convert("RGB").save(str(target) + ".converted", format="PNG")
                Path(str(target) + ".converted").replace(target)
            else:
                request_id = media.get("request_id")
                if not request_id:
                    remaining -= estimate
                    media["submission_state"] = "uncertain"
                    persist()
                    request_id = siliconflow.video_submit(media["prompt"], 5)
                    media["request_id"] = request_id
                    media["submission_state"] = "submitted"
                    event = {"role": "video", "model": model("video"), "seconds": 5, "estimated_cny": str(estimate), "actual_cny": None, "status": "submitted", "request_id": request_id}
                    plan.setdefault("usage_events", []).append(event); persist()
                else:
                    event = next((e for e in reversed(plan.get("usage_events", [])) if e.get("request_id") == request_id), None)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    status, url = siliconflow.video_status(request_id)
                    if status == "Succeed":
                        siliconflow.download(url, target, 30*1024*1024); break
                    update(f"镜头 {idx+1} 的视频素材：{status}")
                    time.sleep(5)
                else:
                    raise siliconflow.ProviderError("视频任务仍在处理，可重试并继续查询原任务。")
            media["asset_id"] = asset_id
            media["submission_state"] = ""
            if (plan.get("preview_job") or {}).get("index") == idx:
                plan["preview_job"]["status"] = "stale"
            if kind == "video": media["request_id"] = ""
            if event: event["status"] = "complete"
            persist()
        except (siliconflow.ProviderError, OSError, ValueError) as exc:
            target.unlink(missing_ok=True)
            if event: event["status"] = "failed_or_pending"
            plan.setdefault("warnings", []).append(f"镜头 {idx+1} 素材未完成，使用程序化动画：{exc}")
            persist()
