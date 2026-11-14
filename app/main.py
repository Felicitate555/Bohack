import json
import os
import shutil
import threading
import time
import uuid
import copy
import subprocess
import tempfile

from starlette.concurrency import run_in_threadpool
from urllib.parse import unquote
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import director, render, browser_render, audio, style_profile, style_library
from .providers import model_registry, siliconflow
from .services import media_assets, speech
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
project_io_lock = threading.RLock()
render_lock = threading.Lock()
render_tasks_lock = threading.RLock()
render_tasks = {}
planning_lock = threading.Lock()
planning_active = set()


class Create(BaseModel):
    topic: str = Field(default="", max_length=500)
    example: bool = False
    target_seconds: int | None = None
    creative_mode: str = "smart"
    style_profile: dict | None = None


class RenderRequest(BaseModel):
    voice: bool = False
    engine: str = "browser"
    quality: str = "preview"
    music_volume: float = Field(default=.18, ge=0, le=1)
    voice_provider: str = "edge"
    voice_name: str = "anna"
    voice_speed: float = Field(default=1.0, ge=.5, le=2)
    generate_media: bool = False
    narration_mode: str = "scene"


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
    with project_io_lock:
        if not path.exists():
            raise HTTPException(404, "项目不存在")
        project = json.loads(path.read_text(encoding="utf-8"))
    with planning_lock:
        active = pid in planning_active
    if (project.get("planning_job") or {}).get("status") == "running" and not active:
        project["planning_job"].update({"status": "error", "message": "上次分镜规划因服务中断而停止；已保存的分镜仍可打开。"})
        save(pid, project)
    if (project.get("job") or {}).get("status") == "running" and not render_lock.locked():
        project["job"].update({"status": "error", "message": "上次渲染中断，请重新生成。"})
        save(pid, project)
    if (project.get("preview_job") or {}).get("status") == "running" and not render_lock.locked():
        project["preview_job"].update({"status": "error", "message": "上次预览中断，请重新生成。"})
        save(pid, project)
    return project


def save(pid, obj):
    path = project_dir(pid) / "project.json"
    with project_io_lock:
        obj["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (".project-" + uuid.uuid4().hex + ".tmp")
        created = False
        try:
            with tmp.open("x", encoding="utf-8") as output:
                created = True
                output.write(json.dumps(obj, ensure_ascii=False, indent=2))
            for attempt in range(4):
                try:
                    tmp.replace(path)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(.05 * (attempt + 1))
        finally:
            if created:
                tmp.unlink(missing_ok=True)


def start_locked_render_worker(pid, plan, work):
    """Transfer the already-held render lock to the worker only on startup."""
    try:
        save(pid, plan)
        threading.Thread(target=work, daemon=True).start()
    except BaseException:
        render_lock.release()
        raise


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg")), "model_configured": bool(model_registry.api_key()), "browser_renderer": browser_render.available()}


@app.get("/api/settings/models")
def model_settings():
    return {"configured": bool(model_registry.api_key()), "models": model_registry.registry()}


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


@app.get("/api/styles")
def styles():
    return style_library.list_profiles(DATA)


@app.post("/api/styles")
def save_style(body: dict):
    try:
        return style_library.save(DATA, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/styles/analyze")
async def analyze_style(request: Request):
    # A private temporary file is removed even on invalid media or interrupted upload.
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reference_", dir=DATA) as folder:
        path = Path(folder) / "reference.mp4"
        size = 0
        with path.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 150 * 1024 * 1024:
                    raise HTTPException(413, "参考视频不能超过 150 MB。")
                output.write(chunk)
        if not size:
            raise HTTPException(400, "请选择参考 MP4。")
        try:
            return await run_in_threadpool(style_library.analyze_reference, path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects")
def projects():
    results = []
    for path in DATA.glob("*/project.json"):
        try:
            with project_io_lock:
                p = json.loads(path.read_text(encoding="utf-8"))
            results.append({k: p.get(k) for k in ("id", "title", "topic", "updated_at", "mode", "job", "planning_job")})
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
        plan, usage, mode = director.validate({**director.example(), "style_profile": body.style_profile}), {}, "example"
    else:
        topic = body.topic.strip()
        if not topic:
            raise HTTPException(400, "先输入一个想制作的主题。")
        try:
            plan, usage = director.generate(topic, body.target_seconds, body.creative_mode, profile=body.style_profile)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        mode = "ai"
    pid = str(uuid.uuid4())
    plan.update({"id": pid, "mode": mode, "creative_mode": body.creative_mode, "usage": usage, "usage_events": usage if isinstance(usage, list) else [], "job": None, "target_seconds": body.target_seconds, "style_profile": style_profile.normalize(body.style_profile)})
    save(pid, plan)
    return plan


@app.post("/api/planning")
def start_planning(body: Create):
    if body.example:
        raise HTTPException(400, "离线示例请直接创建。")
    if body.target_seconds not in (None, 30, 60, 90, 120) or body.creative_mode not in model_registry.MODES:
        raise HTTPException(400, "请选择可用的创作模式和时长。")
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(400, "先输入一个想制作的主题。")
    if not model_registry.api_key() and not (os.getenv("LLM_API_KEY", "").strip() and os.getenv("LLM_MODEL", "").strip()):
        raise HTTPException(400, "请先在 .env 中配置 SILICONFLOW_API_KEY，或选择零密钥示例。")
    pid = str(uuid.uuid4())
    plan = {"id": pid, "title": topic[:80], "topic": topic, "scenes": [], "mode": "ai", "creative_mode": body.creative_mode,
            "target_seconds": body.target_seconds, "job": None, "usage_events": [], "style_profile": style_profile.normalize(body.style_profile),
            "planning_job": {"status": "running", "message": "正在准备创作"}}
    return launch_planning(pid, plan)


def launch_planning(pid, plan):
    topic, seconds, mode = plan["topic"], plan.get("target_seconds"), plan.get("creative_mode", "smart")
    old_events = copy.deepcopy(plan.get("usage_events") or [])
    with planning_lock:
        if pid in planning_active:
            raise HTTPException(409, "该项目正在规划中。")
        planning_active.add(pid)
    try:
        plan["planning_job"] = {"status": "running", "message": "正在准备创作"}
        save(pid, plan)
        def work():
            def progress(message):
                plan["planning_job"]["message"] = message
                save(pid, plan)
            def checkpoint(partial, events):
                if partial["scenes"] or not plan.get("scenes"):
                    plan.update({"title": partial["title"], "scenes": copy.deepcopy(partial["scenes"])})
                plan["usage_events"] = old_events + copy.deepcopy(events)
                if "knowledge_script" in partial:
                    plan["knowledge_script"] = copy.deepcopy(partial["knowledge_script"])
                if "emotion_plan" in partial:
                    plan["emotion_plan"] = copy.deepcopy(partial["emotion_plan"])
                plan["planning_job"]["message"] = "基础分镜已保存，继续优化中" if partial["scenes"] else "编剧草稿已保存，继续规划分镜"
                save(pid, plan)
            try:
                if isinstance(plan.get("knowledge_script"), dict):
                    result, usage = director.generate(topic, seconds, mode, progress, checkpoint, initial_script=plan["knowledge_script"], profile=plan.get("style_profile"))
                else:
                    result, usage = director.generate(topic, seconds, mode, progress, checkpoint, profile=plan.get("style_profile"))
                plan.update(result)
                plan["usage"] = usage
                plan["usage_events"] = old_events + (usage if isinstance(usage, list) else [])
                plan["planning_job"].update({"status": "done", "message": "分镜方案已完成"})
            except Exception as exc:
                plan["planning_job"].update({"status": "error", "message": str(exc)[:600] or "分镜生成失败，请检查服务后重试。"})
            finally:
                try:
                    save(pid, plan)
                finally:
                    with planning_lock:
                        planning_active.discard(pid)
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        with planning_lock:
            planning_active.discard(pid)
        raise
    return {"id": pid, "planning_job": plan["planning_job"]}


@app.post("/api/projects/{pid}/planning/retry")
def retry_planning(pid: str):
    plan = read_project(pid)
    if (plan.get("planning_job") or {}).get("status") != "error":
        raise HTTPException(409, "只有中断或失败的规划任务可以重试。")
    if plan.get("scenes"):
        raise HTTPException(409, "项目已有保存的分镜，请直接编辑，避免重试覆盖。")
    if (plan.get("job") or {}).get("status") == "running" or (plan.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染任务运行中，暂时不能重新规划。")
    return launch_planning(pid, plan)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return read_project(pid)


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


def invalidate_scene_assessments(plan, indices):
    indices = set(indices)
    if not indices:
        return
    emotion = plan.get("emotion_plan")
    if isinstance(emotion, dict):
        emotion["status"] = "stale"
        emotion["message"] = "分镜已修改，原情绪规划可能不再对应，请重新核对。"
    for index in indices:
        review = (plan.get("reviews") or {}).get(str(index))
        if isinstance(review, dict):
            review["stale"] = True


@app.post("/api/projects/{pid}/scenes/{index}/media/reset")
def reset_scene_media(pid: str, index: int):
    plan = read_project(pid)
    scene = get_scene(plan, index)
    if (plan.get("job") or {}).get("status") == "running" or (plan.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "任务运行时不能重置素材。")
    media = scene.get("spec", {}).get("media", {})
    media.update({"asset_id": "", "request_id": "", "submission_state": ""})
    invalidate_scene_assessments(plan, [index])
    if plan.get("job") and plan["job"].get("status") == "done": plan["job"]["stale"] = True
    if (plan.get("preview_job") or {}).get("index") == index: plan["preview_job"]["status"] = "stale"
    save(pid, plan)
    return {"ok": True, "message": "素材状态已重置，下次生成会重新请求素材。"}


@app.post("/api/projects/{pid}/scenes/{index}/improve")
def improve_scene(pid: str, index: int, body: SceneEdit):
    plan = read_project(pid)
    scene = get_scene(plan, index)
    if (plan.get("job") or {}).get("status") == "running" or (plan.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染中无法修改镜头。")
    try:
        emotion = plan.get("emotion_plan") or {}
        beats = [b for b in emotion.get("beats", []) if isinstance(b, dict) and b.get("scene_index") == index] if emotion.get("status") == "generated" else []
        revised, usage = animation_builder.improve(scene, body.instruction, profile=plan.get("style_profile"), emotion_beats=beats)
        revised = animation_builder.preserve_audio_controls(scene, revised)
        validated = director.validate({"title": plan["title"], "topic": plan["topic"], "scenes": [revised], "style_profile": plan.get("style_profile")})["scenes"][0]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Preserve the existing local media; model output never selects file paths.
    validated["spec"]["media"] = scene["spec"]["media"]
    plan["scenes"][index] = validated
    if director.validate({"scenes": [scene], "style_profile": plan.get("style_profile")})["scenes"][0] != validated:
        invalidate_scene_assessments(plan, [index])
    if (plan.get("preview_job") or {}).get("index") == index: plan["preview_job"]["status"] = "stale"
    plan.setdefault("usage_events", []).append(usage)
    if plan.get("job") and plan["job"].get("status") == "done": plan["job"]["stale"] = True
    save(pid, plan)
    return plan


@app.post("/api/projects/{pid}/scenes/{index}/preview")
def preview_scene(pid: str, index: int):
    plan = read_project(pid)
    get_scene(plan, index)
    scene = director.validate(plan)["scenes"][index]
    issues = director.visual_issues({"scenes": [scene]})
    if issues:
        raise HTTPException(400, "；".join(issues) + "。请修改相应参数或选择其他视觉类型。")
    plan["scenes"][index] = scene
    if not browser_render.available():
        raise HTTPException(400, "镜头预览需要 Playwright 和 Chromium。")
    if not render_lock.acquire(blocking=False):
        raise HTTPException(409, "已有任务运行，请稍后再试。")
    preview_id = uuid.uuid4().hex[:12]
    plan["preview_job"] = {"status": "running", "index": index, "id": preview_id, "message": "正在预览镜头"}
    def work():
        def update(message):
            plan["preview_job"]["message"] = message
            save(pid, plan)
        try:
            single = {"scenes": [copy.deepcopy(scene)], "target_seconds": None, "style_profile": plan.get("style_profile")}
            duration = browser_render.render(single, project_dir(pid) / ("preview_" + preview_id), False, update)
            preview_warnings = []
            for warning in single.get("warnings", []):
                warning = warning.replace("镜头 1 ", f"镜头 {index+1} ", 1)
                preview_warnings.append(warning)
                if warning not in plan.setdefault("warnings", []): plan["warnings"].append(warning)
            message = "镜头预览已完成；素材存在回退，请查看提示。" if preview_warnings else "镜头预览已完成"
            plan["preview_job"].update({"status": "done", "duration": duration, "message": message, "warnings": preview_warnings})
        except Exception as exc:
            plan["preview_job"].update({"status": "error", "message": str(exc)[:400]})
        finally:
            try: save(pid, plan)
            finally: render_lock.release()
    start_locked_render_worker(pid, plan, work)
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
        emotion = plan.get("emotion_plan") or {}
        beats = [b for b in emotion.get("beats", []) if isinstance(b, dict) and b.get("scene_index") == index] if emotion.get("status") == "generated" else []
        result, usage = visual_reviewer.review(scene, frames, profile=plan.get("style_profile"), emotion_beats=beats)
    except (ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(400, "审片失败，请检查网络、模型权限和预览视频。") from exc
    if isinstance(result, dict):
        result["stale"] = False
    plan.setdefault("reviews", {})[str(index)] = result
    plan.setdefault("usage_events", []).append(usage)
    save(pid, plan)
    return result


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: dict):
    old = read_project(pid)
    if (old.get("planning_job") or {}).get("status") == "running":
        raise HTTPException(409, "分镜规划中，请等待完成后编辑。")
    if (old.get("job") or {}).get("status") == "running" or (old.get("preview_job") or {}).get("status") == "running":
        raise HTTPException(409, "渲染中无法修改，请等待完成。")
    try:
        plan = director.validate({**body, "style_profile": body.get("style_profile", old.get("style_profile"))})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = old.get("job")
    if job and job.get("status") == "done":
        job["stale"] = True
    plan.update({"id": pid, "mode": old["mode"], "creative_mode": old.get("creative_mode", "smart"), "usage": old.get("usage", {}), "usage_events": old.get("usage_events", []), "knowledge_script": old.get("knowledge_script"), "warnings": old.get("warnings", []), "job": job, "music": old.get("music"), "target_seconds": old.get("target_seconds"), "edited": True})
    if isinstance(old.get("emotion_plan"), dict):
        plan["emotion_plan"] = copy.deepcopy(old["emotion_plan"])
    plan["style_profile"] = style_profile.normalize(body.get("style_profile", old.get("style_profile")))
    if style_profile.normalize(old.get("style_profile")) != plan["style_profile"] and any((s.get("spec", {}).get("media") or {}).get("asset_id") for s in plan["scenes"]):
        notice = "风格已更改，已有素材仍沿用原文件；如需新风格，请在对应镜头主动重置素材后重新生成。"
        if notice not in plan["warnings"]: plan["warnings"].append(notice)
    plan["preview_job"] = old.get("preview_job")
    plan["reviews"] = copy.deepcopy(old.get("reviews", {}))
    old_scenes = director.validate(old)["scenes"]
    style_changed = style_profile.normalize(old.get("style_profile")) != plan["style_profile"]
    changed = [i for i, scene in enumerate(plan["scenes"]) if style_changed or i >= len(old_scenes) or old_scenes[i] != scene]
    changed += list(range(len(plan["scenes"]), len(old_scenes)))
    invalidate_scene_assessments(plan, changed)
    plan["planning_job"] = old.get("planning_job")
    preview = plan.get("preview_job") or {}
    i = preview.get("index")
    if preview.get("status") == "done" and isinstance(i, int) and (style_profile.normalize(old.get("style_profile")) != plan["style_profile"] or i >= len(plan["scenes"]) or director.validate(old)["scenes"][i] != plan["scenes"][i]):
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
    if (plan.get("planning_job") or {}).get("status") == "running":
        raise HTTPException(409, "分镜规划中，请等待完成后生成视频。")
    if body.engine not in {"browser", "light"} or body.quality not in {"preview", "full"}:
        raise HTTPException(400, "不支持该渲染选项。")
    if body.voice_provider not in {"edge", "siliconflow", "moss"}:
        raise HTTPException(400, "不支持该配音方式。")
    if body.narration_mode not in {"scene", "sentence"}:
        raise HTTPException(400, "不支持该字幕对齐方式。")
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
    issues = director.visual_issues(plan)
    if issues:
        raise HTTPException(400, "；".join(issues) + "。请使用 AI 优化对应镜头或选择其他视觉类型，再生成视频。")
    if body.voice and body.narration_mode == "sentence" and sum(len(speech.sentence_chunks(s["narration"])) for s in plan["scenes"]) > 32:
        raise HTTPException(400, "逐句配音最多 32 段，请缩短口播或选择整镜配音。")
    light_visuals = {"binary_search", "question", "dots", "network", "formula", "compare", "flow", "ending", "distinct"}
    unsupported = [str(i + 1) for i, scene in enumerate(plan["scenes"]) if scene["visual"] not in light_visuals]
    if body.engine == "light" and unsupported:
        raise HTTPException(400, "轻量渲染暂不支持镜头 " + "、".join(unsupported) + " 的动效，请选择浏览器渲染器或修改这些镜头。")
    if not body.generate_media and any((s.get("spec", {}).get("media") or {}).get("kind") in {"image", "video"} and not (s.get("spec", {}).get("media") or {}).get("asset_id") for s in plan["scenes"]):
        message = "部分镜头原本计划使用 AI 素材，但此次未开启素材生成，已使用程序动画。"
        if message not in plan.setdefault("warnings", []): plan["warnings"].append(message)
    if not render_lock.acquire(blocking=False):
        raise HTTPException(409, "已有视频正在渲染，请等待完成后再试。")
    run_id = uuid.uuid4().hex[:12]
    old_job = plan.get("job") or {}
    last_completed = ({k: old_job.get(k) for k in ("run_id", "duration", "voice", "stale", "narration_mode")}
                      if old_job.get("status") == "done" else old_job.get("last_completed"))
    previous_run = (last_completed or {}).get("run_id")
    job = {"status": "running", "message": "准备素材", "run_id": run_id, "voice": body.voice, "narration_mode": body.narration_mode, "last_completed": last_completed}
    plan["job"] = job
    cancelled = threading.Event()

    def work():
        def check_cancel():
            if cancelled.is_set():
                raise render.RenderCancelled()
        def update(message):
            with render_tasks_lock:
                check_cancel()
                plan["job"]["message"] = message
                save(pid, plan)
        update.check_cancel = check_cancel
        def persist():
            with render_tasks_lock:
                save(pid, plan)
        def report(event):
            with render_tasks_lock:
                plan.setdefault("usage_events", []).append(event)
                save(pid, plan)
        error, duration = None, None
        try:
            check_cancel()
            if body.generate_media:
                media_assets.prepare(plan, project_dir(pid), update, persist)
            check_cancel()
            if body.engine == "browser":
                music = project_dir(pid) / "music.bin" if plan.get("music") else None
                duration = browser_render.render(plan, project_dir(pid) / run_id, body.voice, update, body.quality, music, (plan.get("music") or {}).get("analysis"), body.music_volume, body.voice_provider, body.voice_name, body.voice_speed, report, previous_run, narration_mode=body.narration_mode)
            else:
                duration = render.render(plan, project_dir(pid) / run_id, body.voice, update, plan.get("target_seconds"), body.voice_provider, body.voice_name, body.voice_speed, report, narration_mode=body.narration_mode)
        except Exception as exc:
            error = exc
        finally:
            try:
                with render_tasks_lock:
                    if cancelled.is_set():
                        message = "渲染已取消。" + ("之前的成片仍可使用。" if last_completed else "")
                        plan["job"].update({"status": "cancelled", "message": message})
                    elif error is not None:
                        plan["job"].update({"status": "error", "message": str(error)[:600]})
                    else:
                        plan["job"].update({"status": "done", "message": "成片已完成", "duration": duration})
                    try: save(pid, plan)
                    finally: render_tasks.pop(pid, None)
            finally:
                render_lock.release()
    with render_tasks_lock:
        render_tasks[pid] = (cancelled, plan)
        try:
            start_locked_render_worker(pid, plan, work)
        except BaseException:
            render_tasks.pop(pid, None)
            raise
    return job


@app.post("/api/projects/{pid}/render/cancel")
def cancel_render(pid: str):
    project_dir(pid)
    with render_tasks_lock:
        task = render_tasks.get(pid)
        if not task:
            raise HTTPException(409, "没有可取消的渲染任务。")
        cancelled, plan = task
        cancelled.set()
        plan["job"]["cancel_requested"] = True
        plan["job"]["message"] = "正在取消，等待当前请求或合成步骤结束。"
        save(pid, plan)
        return plan["job"]


@app.get("/api/projects/{pid}/export/{kind}")
def export(pid: str, kind: str):
    p = read_project(pid)
    if kind == "json":
        with project_io_lock:
            content = (project_dir(pid) / "project.json").read_bytes()
        return Response(content=content, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="vibelab-project.json"'})
    job = p.get("job") or {}
    files = {"mp4": ("video.mp4", "video/mp4"), "srt": ("subtitles.srt", "application/x-subrip")}
    completed = job if job.get("status") == "done" else (job.get("last_completed") or {})
    run_id = completed.get("run_id", "")
    if kind not in files or len(run_id) != 12 or any(c not in "0123456789abcdef" for c in run_id):
        raise HTTPException(404, "文件尚未生成")
    filename, media = files[kind]
    path = project_dir(pid) / run_id / filename
    if not path.exists():
        raise HTTPException(404, "文件不存在，请重新渲染")
    return FileResponse(path, media_type=media, filename=filename if kind == "srt" else None)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
