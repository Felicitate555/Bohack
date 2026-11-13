import json
import os
import shutil
import threading
import uuid
import copy
import subprocess
from urllib.parse import unquote
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import director, render, browser_render, audio
from .providers import model_registry, siliconflow
from .services import media_assets
from .agents import visual_reviewer, animation_builder

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("VIBELAB_DATA", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
envfile = ROOT / ".env"
if envfile.exists():
    for line in envfile.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

app = FastAPI(title="VibeLab")
render_lock = threading.Lock()


class Create(BaseModel):
    topic: str = Field(default="", max_length=500)
    example: bool = False
    target_seconds: int | None = None
    creative_mode: str = "smart"


class RenderRequest(BaseModel):
    voice: bool = False
    engine: str = "browser"
    quality: str = "preview"
    music_volume: float = Field(default=.18, ge=0, le=1)
    voice_provider: str = "edge"
    voice_name: str = "anna"
    voice_speed: float = Field(default=1.0, ge=.5, le=2)
    generate_media: bool = False
    media_budget_cny: float = Field(default=0, ge=0, le=100)


class SceneEdit(BaseModel):
    instruction: str = Field(default="增强画面与知识解释的对应关系", max_length=500)


def project_dir(pid):
    try:
        uuid.UUID(pid)
    except ValueError:
        raise HTTPException(404, "项目不存在")
    return DATA / pid


def read_project(pid):
    path = project_dir(pid) / "project.json"
    if not path.exists():
        raise HTTPException(404, "项目不存在")
    project = json.loads(path.read_text(encoding="utf-8"))
    if (project.get("job") or {}).get("status") == "running" and not render_lock.locked():
        project["job"].update({"status": "error", "message": "上次渲染中断，请重新生成。"})
        save(pid, project)
    if (project.get("preview_job") or {}).get("status") == "running" and not render_lock.locked():
        project["preview_job"].update({"status": "error", "message": "上次预览中断，请重新生成。"})
        save(pid, project)
    return project


def save(pid, obj):
    obj["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = project_dir(pid) / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg")), "model_configured": bool(model_registry.api_key()), "browser_renderer": browser_render.available()}


@app.get("/api/settings/models")
def model_settings():
    return {"configured": bool(model_registry.api_key()), "models": model_registry.registry(), "prices_configured": {"image": media_assets.price("SF_PRICE_IMAGE_CNY") is not None, "video": media_assets.price("SF_PRICE_VIDEO_SECOND_CNY") is not None}}


@app.post("/api/settings/check")
def check_model_connection():
    if not model_registry.api_key():
        raise HTTPException(400, "请先填写 SILICONFLOW_API_KEY 并重启服务。")
    try:
        response = __import__("requests").get(model_registry.base_url()+"/models", headers={"Authorization":"Bearer "+model_registry.api_key()}, timeout=15)
        if not response.ok:
            raise ValueError(f"HTTP {response.status_code}")
        return {"connected": True, "message": "模型列表接口连接成功；具体模型权限需实际调用时确认。"}
    except Exception as exc:
        raise HTTPException(400, "连接失败，请检查网络、密钥和服务地址。") from exc


@app.get("/api/projects")
def projects():
    results = []
    for path in DATA.glob("*/project.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
            results.append({k: p.get(k) for k in ("id", "title", "topic", "updated_at", "mode", "job")})
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(results, key=lambda p: p.get("updated_at") or "", reverse=True)


@app.post("/api/projects")
def create(body: Create):
    if body.target_seconds not in (None, 30, 60, 90, 120):
        raise HTTPException(400, "请选择可用的视频时长。")
    if body.creative_mode not in model_registry.MODES:
        raise HTTPException(400, "请选择有效的创作模式。")
    if body.example:
        plan, usage, mode = director.validate(director.example()), {}, "example"
    else:
        topic = body.topic.strip()
        if not topic:
            raise HTTPException(400, "先输入一个想制作的主题。")
        try:
            plan, usage = director.generate(topic, body.target_seconds, body.creative_mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        mode = "ai"
    pid = str(uuid.uuid4())
    plan.update({"id": pid, "mode": mode, "creative_mode": body.creative_mode, "usage": usage, "usage_events": usage if isinstance(usage, list) else [], "job": None, "target_seconds": body.target_seconds})
    save(pid, plan)
    return plan


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return read_project(pid)


@app.get("/api/projects/{pid}/cost-plan")
def cost_plan(pid: str):
    plan = read_project(pid)
    mode = plan.get("creative_mode", "smart")
    scenes = director.validate(plan)["scenes"]
    kinds = [(s.get("spec", {}).get("media") or {}).get("kind") for s in scenes]
    images = min(kinds.count("image"), 1) if mode != "fast" else 0
    videos = min(kinds.count("video"), 1) if mode == "director" else 0
    image_price, video_price = media_assets.price("SF_PRICE_IMAGE_CNY"), media_assets.price("SF_PRICE_VIDEO_SECOND_CNY")
    known = (not images or image_price is not None) and (not videos or video_price is not None)
    estimate = float((images * (image_price or 0)) + (videos * 5 * (video_price or 0))) if known else None
    return {"local_only": {"images": 0, "video_seconds": 0, "media_cny": 0}, "mixed": {"images": images, "video_seconds": videos*5, "media_cny": estimate}, "scope": "仅媒体估算，未含文本、配音、审片及计算成本"}


def get_scene(plan, index):
    if index < 0 or index >= len(plan["scenes"]):
        raise HTTPException(404, "镜头不存在。")
    return plan["scenes"][index]


@app.get("/api/projects/{pid}/scenes/{index}/asset")
def scene_asset(pid: str, index: int):
    scene = get_scene(read_project(pid), index)
    media = scene.get("spec", {}).get("media", {})
    ident, kind = media.get("asset_id"), media.get("kind")
    if not ident or not __import__("re").fullmatch(r"[a-f0-9]{32}", ident) or kind not in {"image", "video"}:
        raise HTTPException(404, "没有可预览的素材。")
    path = project_dir(pid) / "assets" / (ident + (".png" if kind == "image" else ".mp4"))
    if not path.is_file():
        raise HTTPException(404, "素材不存在。")
    return FileResponse(path, media_type="image/png" if kind == "image" else "video/mp4")


@app.post("/api/projects/{pid}/scenes/{index}/media/reset")
def reset_scene_media(pid: str, index: int):
    plan = read_project(pid)
    scene = get_scene(plan, index)
    if (plan.get("job") or {}).get("status") == "running" or (plan.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "任务运行时不能重置素材。")
    media = scene.get("spec", {}).get("media", {})
    media.update({"asset_id": "", "request_id": "", "submission_state": ""})
    if plan.get("job") and plan["job"].get("status") == "done": plan["job"]["stale"] = True
    if (plan.get("preview_job") or {}).get("index") == index: plan["preview_job"]["status"] = "stale"
    save(pid, plan)
    return {"ok": True, "message": "素材状态已重置，下次生成可能再次计费。"}


@app.post("/api/projects/{pid}/scenes/{index}/improve")
def improve_scene(pid: str, index: int, body: SceneEdit):
    plan = read_project(pid)
    scene = get_scene(plan, index)
    if (plan.get("job") or {}).get("status") == "running" or (plan.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染中无法修改镜头。")
    try:
        revised, usage = animation_builder.improve(scene, body.instruction)
        validated = director.validate({"title": plan["title"], "topic": plan["topic"], "scenes": [revised]})["scenes"][0]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Preserve the existing local media; model output never selects file paths.
    validated["spec"]["media"] = scene["spec"]["media"]
    plan["scenes"][index] = validated
    if (plan.get("preview_job") or {}).get("index") == index: plan["preview_job"]["status"] = "stale"
    plan.setdefault("usage_events", []).append(usage)
    if plan.get("job") and plan["job"].get("status") == "done": plan["job"]["stale"] = True
    save(pid, plan)
    return plan


@app.post("/api/projects/{pid}/scenes/{index}/preview")
def preview_scene(pid: str, index: int):
    plan = read_project(pid)
    scene = get_scene(plan, index)
    if not browser_render.available():
        raise HTTPException(400, "镜头预览需要 Playwright 和 Chromium。")
    if not render_lock.acquire(blocking=False):
        raise HTTPException(409, "已有任务运行，请稍后再试。")
    preview_id = uuid.uuid4().hex[:12]
    plan["preview_job"] = {"status": "running", "index": index, "id": preview_id, "message": "正在预览镜头"}
    save(pid, plan)
    def work():
        def update(message):
            plan["preview_job"]["message"] = message
            save(pid, plan)
        try:
            single = {"scenes": [copy.deepcopy(scene)], "target_seconds": None}
            duration = browser_render.render(single, project_dir(pid) / ("preview_" + preview_id), False, update)
            plan["preview_job"].update({"status": "done", "duration": duration, "message": "镜头预览已完成"})
        except Exception as exc:
            plan["preview_job"].update({"status": "error", "message": str(exc)[:400]})
        finally:
            try: save(pid, plan)
            finally: render_lock.release()
    threading.Thread(target=work, daemon=True).start()
    return plan["preview_job"]


@app.get("/api/projects/{pid}/scenes/{index}/preview")
def get_scene_preview(pid: str, index: int):
    plan = read_project(pid)
    get_scene(plan, index)
    preview = plan.get("preview_job") or {}
    if preview.get("status") != "done" or preview.get("index") != index:
        raise HTTPException(404, "该镜头尚无预览。")
    return FileResponse(project_dir(pid) / ("preview_" + preview["id"]) / "video.mp4", media_type="video/mp4")


@app.post("/api/projects/{pid}/scenes/{index}/review")
def review_scene(pid: str, index: int):
    plan = read_project(pid)
    scene = get_scene(plan, index)
    preview = plan.get("preview_job") or {}
    if preview.get("status") != "done" or preview.get("index") != index:
        raise HTTPException(400, "请先生成这个镜头的预览。")
    folder = project_dir(pid) / ("preview_" + preview["id"])
    frames = []
    try:
        for i, fraction in enumerate((.2, .5, .8)):
            frame = folder / f"review_{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(preview["duration"]*fraction), "-i", str(folder / "video.mp4"), "-frames:v", "1", "-vf", "scale=640:-2", str(frame)], check=True, timeout=30, capture_output=True)
            frames.append(frame)
        result, usage = visual_reviewer.review(scene, frames)
    except (ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(400, "审片失败，请检查网络、模型权限和预览视频。") from exc
    plan.setdefault("reviews", {})[str(index)] = result
    plan.setdefault("usage_events", []).append(usage)
    save(pid, plan)
    return result


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: dict):
    old = read_project(pid)
    if (old.get("job") or {}).get("status") == "running" or (old.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染中无法修改，请等待完成。")
    try:
        plan = director.validate(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = old.get("job")
    if job and job.get("status") == "done":
        job["stale"] = True
    plan.update({"id": pid, "mode": old["mode"], "creative_mode": old.get("creative_mode", "smart"), "usage": old.get("usage", {}), "usage_events": old.get("usage_events", []), "knowledge_script": old.get("knowledge_script"), "warnings": old.get("warnings", []), "job": job, "music": old.get("music"), "target_seconds": old.get("target_seconds"), "edited": True})
    plan["preview_job"] = old.get("preview_job")
    plan["reviews"] = old.get("reviews", {})
    preview = plan.get("preview_job") or {}
    i = preview.get("index")
    if preview.get("status") == "done" and isinstance(i, int) and (i >= len(plan["scenes"]) or director.validate(old)["scenes"][i] != plan["scenes"][i]):
        preview["status"] = "stale"
    save(pid, plan)
    return plan


@app.post("/api/projects/{pid}/music")
async def upload_music(pid: str, request: Request):
    plan = read_project(pid)
    if (plan.get("job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染中无法更换音乐。")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 25 * 1024 * 1024:
            raise HTTPException(413, "音乐文件不能超过 25 MB。")
        chunks.append(chunk)
    raw = b"".join(chunks)
    is_wav = raw.startswith(b"RIFF") and raw[8:12] == b"WAVE"
    is_mp3 = raw.startswith(b"ID3") or (len(raw)>2 and raw[0]==255 and raw[1]&0xe0==0xe0)
    if not (is_wav or is_mp3):
        raise HTTPException(400, "请选择有效的 MP3 或 WAV 文件。")
    temp = project_dir(pid) / "music_upload.tmp"
    temp.write_bytes(raw)
    try:
        result = audio.analyze(temp)
    except ValueError as exc:
        temp.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    dest = project_dir(pid) / "music.bin"
    temp.replace(dest)
    name = unquote(request.headers.get("x-file-name", "本地音乐")).replace("\\", "/").split("/")[-1][:100]
    plan["music"] = {"name": name or "本地音乐", "analysis": result, "format": "wav" if is_wav else "mp3"}
    if plan.get("job") and plan["job"].get("status") == "done":
        plan["job"]["stale"] = True
    save(pid, plan)
    return plan["music"]


@app.delete("/api/projects/{pid}/music")
def remove_music(pid: str):
    plan = read_project(pid)
    if (plan.get("job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染中无法移除音乐。")
    (project_dir(pid) / "music.bin").unlink(missing_ok=True)
    plan["music"] = None
    if plan.get("job") and plan["job"].get("status") == "done":
        plan["job"]["stale"] = True
    save(pid, plan)
    return {"ok": True}


@app.get("/api/projects/{pid}/music")
def get_music(pid: str):
    plan = read_project(pid)
    path = project_dir(pid) / "music.bin"
    if not plan.get("music") or not path.exists():
        raise HTTPException(404, "未上传音乐")
    return FileResponse(path, media_type="audio/wav" if plan["music"]["format"] == "wav" else "audio/mpeg")


@app.post("/api/projects/{pid}/render")
def start_render(pid: str, body: RenderRequest):
    plan = read_project(pid)
    if body.engine not in {"browser", "light"} or body.quality not in {"preview", "full"}:
        raise HTTPException(400, "不支持该渲染选项。")
    if body.voice_provider not in {"edge", "siliconflow", "moss"}:
        raise HTTPException(400, "不支持该配音方式。")
    if body.voice and body.voice_provider != "edge" and not model_registry.api_key():
        raise HTTPException(400, "硅基流动配音需要配置 API Key。")
    if body.generate_media and body.engine == "light":
        raise HTTPException(400, "AI 素材合成需要浏览器渲染器。")
    if body.engine == "light" and plan.get("music"):
        raise HTTPException(400, "音乐驱动需要浏览器渲染器；轻量渲染不支持音乐。")
    if body.engine == "browser" and not browser_render.available():
        raise HTTPException(400, "浏览器渲染器不可用：请用 Docker 启动，或在本机安装 Playwright 和 Chromium。")
    # Bring older projects forward without modifying their stored text.
    validated = director.validate(plan)
    plan["scenes"] = validated["scenes"]
    if not render_lock.acquire(blocking=False):
        raise HTTPException(409, "已有视频正在渲染，请等待完成后再试。")
    run_id = uuid.uuid4().hex[:12]
    previous_run = (plan.get("job") or {}).get("run_id") if (plan.get("job") or {}).get("status") == "done" else None
    job = {"status": "running", "message": "准备素材", "run_id": run_id, "voice": body.voice}
    plan["job"] = job
    save(pid, plan)

    def work():
        def update(message):
            plan["job"]["message"] = message
            save(pid, plan)
        def report(event):
            plan.setdefault("usage_events", []).append(event)
            save(pid, plan)
        try:
            if body.generate_media:
                media_assets.prepare(plan, project_dir(pid), body.media_budget_cny, update, lambda:save(pid,plan))
            if body.engine == "browser":
                music = project_dir(pid) / "music.bin" if plan.get("music") else None
                duration = browser_render.render(plan, project_dir(pid) / run_id, body.voice, update, body.quality, music, (plan.get("music") or {}).get("analysis"), body.music_volume, body.voice_provider, body.voice_name, body.voice_speed, report, previous_run)
            else:
                duration = render.render(plan, project_dir(pid) / run_id, body.voice, update, plan.get("target_seconds"), body.voice_provider, body.voice_name, body.voice_speed, report)
            plan["job"].update({"status": "done", "message": "成片已完成", "duration": duration})
        except Exception as exc:
            plan["job"].update({"status": "error", "message": str(exc)[:600]})
        finally:
            try: save(pid, plan)
            finally: render_lock.release()
    threading.Thread(target=work, daemon=True).start()
    return job


@app.get("/api/projects/{pid}/export/{kind}")
def export(pid: str, kind: str):
    p = read_project(pid)
    if kind == "json":
        return FileResponse(project_dir(pid) / "project.json", filename="vibelab-project.json", media_type="application/json")
    job = p.get("job") or {}
    files = {"mp4": ("video.mp4", "video/mp4"), "srt": ("subtitles.srt", "application/x-subrip")}
    if kind not in files or job.get("status") != "done":
        raise HTTPException(404, "文件尚未生成")
    filename, media = files[kind]
    path = project_dir(pid) / job["run_id"] / filename
    if not path.exists():
        raise HTTPException(404, "文件不存在，请重新渲染")
    return FileResponse(path, media_type=media, filename=filename if kind == "srt" else None)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
