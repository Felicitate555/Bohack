import time
import uuid
from pathlib import Path
from PIL import Image
from ..providers import siliconflow
from ..providers.model_registry import model
from .. import style_profile


def prepare(plan, directory, update, persist):
    """Generate selected scene assets and retain request state for safe resumption."""
    if not any((s.get("spec", {}).get("media") or {}).get("kind") in {"image", "video"} for s in plan["scenes"]):
        plan.setdefault("warnings", []).append("分镜中没有选择 AI 图片或视频素材，因此未调用生图或视频模型。")
        persist()
        return
    check_cancel = getattr(update, "check_cancel", lambda: None)
    asset_dir = directory / "assets"
    asset_dir.mkdir(exist_ok=True)
    for idx, scene in enumerate(plan["scenes"]):
        check_cancel()
        media = scene["spec"]["media"]
        kind = media.get("kind")
        if kind not in {"image", "video"}:
            continue
        if media.get("asset_id") and (asset_dir / (media["asset_id"] + (".mp4" if kind == "video" else ".png"))).is_file():
            continue
        if media.get("submission_state") == "uncertain" and not media.get("request_id"):
            plan.setdefault("warnings", []).append(f"镜头 {idx+1} 上一次素材请求结果不明确，已停止重复提交；请检查平台任务后决定是否重置。")
            persist(); continue
        if not media.get("prompt") and not (kind == "video" and media.get("request_id")):
            plan.setdefault("warnings", []).append(f"镜头 {idx+1} 未填写素材描述，已使用程序动画。")
            persist(); continue
        asset_id = uuid.uuid4().hex
        event = None
        target = asset_dir / (asset_id + (".mp4" if kind == "video" else ".png"))
        styled_prompt = style_profile.media_prompt(media.get("prompt", ""), plan.get("style_profile"))
        update(f"正在制作镜头 {idx+1} 的{'图片' if kind == 'image' else '视频'}素材")
        try:
            if kind == "image":
                image_role = "image_fast" if plan.get("creative_mode") == "fast" else "image"
                event = {"role": "image", "model": model(image_role), "status": "submitted"}
                media["submission_state"] = "uncertain"
                plan.setdefault("usage_events", []).append(event); persist()
                check_cancel()
                url = siliconflow.image(styled_prompt, role=image_role)
                check_cancel()
                siliconflow.download(url, target, 30*1024*1024)
                check_cancel()
                with Image.open(target) as img:
                    if img.width * img.height > 20_000_000:
                        raise ValueError("图片尺寸过大")
                    img.convert("RGB").save(str(target) + ".converted", format="PNG")
                Path(str(target) + ".converted").replace(target)
            else:
                request_id = media.get("request_id")
                if not request_id:
                    media["submission_state"] = "uncertain"
                    persist()
                    check_cancel()
                    request_id = siliconflow.video_submit(styled_prompt, 5)
                    media["request_id"] = request_id
                    media["submission_state"] = "submitted"
                    event = {"role": "video", "model": model("video"), "seconds": 5, "status": "submitted", "request_id": request_id}
                    plan.setdefault("usage_events", []).append(event); persist()
                else:
                    event = next((e for e in reversed(plan.get("usage_events", [])) if e.get("request_id") == request_id), None)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    check_cancel()
                    status, url = siliconflow.video_status(request_id)
                    check_cancel()
                    if status == "Succeed":
                        siliconflow.download(url, target, 30*1024*1024)
                        check_cancel()
                        break
                    update(f"镜头 {idx+1} 的视频素材：{status}")
                    time.sleep(5)
                else:
                    raise siliconflow.ProviderError("视频任务仍在处理，可重试并继续查询原任务。")
            media["asset_id"] = asset_id
            media["submission_state"] = ""
            review = (plan.get("reviews") or {}).get(str(idx))
            if isinstance(review, dict): review["stale"] = True
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
