"""Frame-accurate browser renderer for validated scenes; no model code is run."""
import asyncio
import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from .render import run, split_text, stamp
from .services import speech


def browser_path():
    for candidate in [os.getenv("VIBELAB_CHROMIUM", ""), shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def available():
    try:
        import playwright.sync_api  # noqa
        return bool(browser_path())
    except ImportError:
        return False


def render(project, outdir, voice, update, quality="preview", music=None, beat_timeline=None, music_volume=.18, voice_provider="edge", voice_name="anna", voice_speed=1.0, report=lambda event: None, previous_run=None):
    if not available():
        raise RuntimeError("高质量渲染需要安装 Playwright 和 Chromium；请使用 Docker 启动或选择轻量渲染。")
    from playwright.sync_api import sync_playwright
    fps = 30 if quality == "full" else 15
    w, h = (1920, 1080) if quality == "full" else (960, 540)
    outdir.mkdir(parents=True, exist_ok=True)
    scenes = project["scenes"]
    clips, captions, elapsed = [], [], 0.0
    previous_dir = outdir.parent / previous_run if previous_run and len(previous_run) == 12 and all(c in '0123456789abcdef' for c in previous_run) else None
    old_manifest = {}
    if previous_dir:
        try:
            old_manifest = json.loads((previous_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    old_scenes = old_manifest.get("scenes", [])
    manifest = {"version": 1, "scenes": []}
    beats = (beat_timeline or {}).get("beats", [])
    strong_beats = (beat_timeline or {}).get("strong_beats", [])
    # The synchronous Playwright API runs an event loop in this thread. Produce
    # narration beforehand so asyncio.run() never nests inside that loop.
    voice_durations = []
    if voice:
        try:
            pending = []
            for i, scene in enumerate(scenes):
                old = old_scenes[i] if i < len(old_scenes) else {}
                voice_key = [scene["narration"], voice_provider, voice_name, voice_speed]
                source = previous_dir / f"voice_{i}.mp3" if previous_dir else None
                if old.get("voice_key") == voice_key and source and source.is_file():
                    shutil.copyfile(source, outdir / f"voice_{i}.mp3")
                else:
                    pending.append(i)
            if pending:
                speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report, pending)
            for i in range(len(scenes)):
                info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(outdir / f"voice_{i}.mp3")], capture_output=True, text=True, check=True, timeout=20)
                voice_durations.append(float(info.stdout.strip()))
        except Exception as exc:
            raise RuntimeError("在线配音失败，请检查网络或关闭配音。") from exc
    minimums = [max(3.0, voice_durations[i] + .6) if voice else max(4.0, min(12.0, len(s["narration"]) / 4.2 + .8)) for i, s in enumerate(scenes)]
    extension = max(0, (project.get("target_seconds") or 0) - sum(minimums)) / len(scenes)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path(), headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.set_content((Path(__file__).parent / "scene_canvas.html").read_text(encoding="utf-8"), wait_until="load")
            page.evaluate("document.fonts.ready")
            for i, scene in enumerate(scenes):
                update(f"正在制作镜头 {i+1}/{len(scenes)}：{scene['heading']}")
                audio = outdir / f"voice_{i}.mp3"
                duration = minimums[i] + extension
                # Never truncate narration: move scene end forward to a nearby measured beat.
                boundary = elapsed + duration
                candidates = [b for b in beats if boundary <= b <= boundary + .65]
                if candidates:
                    duration = candidates[0] - elapsed
                frames = math.ceil(duration * fps)
                duration = frames / fps
                local_beats = [b-elapsed for b in beats if elapsed <= b < elapsed+duration]
                local_strong = [b-elapsed for b in strong_beats if elapsed <= b < elapsed+duration]
                speech_duration = min(duration, voice_durations[i]) if voice else duration
                scene["spec"]["timing"] = {"start": round(elapsed, 3), "end": round(elapsed+duration, 3), "speech_duration": round(speech_duration, 3)}
                hash_scene = dict(scene)
                hash_scene["spec"] = {k:v for k,v in scene["spec"].items() if k != "timing"}
                hash_input = {"scene":hash_scene, "index":i, "count":len(scenes), "duration":duration, "beats":local_beats, "strong":local_strong, "fps":fps, "width":w, "height":h, "voice":[voice,voice_provider,voice_name,voice_speed]}
                fingerprint = hashlib.sha256(json.dumps(hash_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                old = old_scenes[i] if i < len(old_scenes) else {}
                prior_clip = previous_dir / f"scene_{i}.mp4" if previous_dir else None
                media = scene["spec"].get("media") or {}
                asset_id, kind = media.get("asset_id"), media.get("kind")
                source = outdir.parent / "assets" / (str(asset_id) + (".mp4" if kind == "video" else ".png")) if asset_id and kind in {"image", "video"} else None
                cacheable = not source or source.is_file()
                if source and source.is_file():
                    mime = "video/mp4" if kind == "video" else "image/png"
                    payload = "data:" + mime + ";base64," + base64.b64encode(source.read_bytes()).decode("ascii")
                    try:
                        page.evaluate("a => window.loadMedia(a[0],a[1])", [kind, payload])
                    except Exception:
                        cacheable = False
                        project.setdefault("warnings", []).append(f"镜头 {i+1} 素材加载失败，已回退程序动画。")
                        page.evaluate("() => window.loadMedia('', '')")
                else:
                    page.evaluate("() => window.loadMedia('', '')")
                clip = outdir / f"scene_{i}.mp4"
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-vcodec", "mjpeg", "-i", "pipe:0"]
                if voice:
                    cmd += ["-i", str(audio), "-af", "apad", "-c:a", "aac", "-b:a", "96k"]
                cmd += ["-t", str(duration), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip)]
                if cacheable and old.get("fingerprint") == fingerprint and prior_clip and prior_clip.is_file():
                    update(f"复用未修改的镜头 {i+1}/{len(scenes)}")
                    shutil.copyfile(prior_clip, clip)
                else:
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    try:
                        for frame in range(frames):
                            args = [scene, i, len(scenes), frame/fps, duration, local_beats, local_strong, w, h]
                            try:
                                page.evaluate("a => window.renderFrame(...a)", args)
                            except Exception:
                                cacheable = False
                                project.setdefault("warnings", []).append(f"镜头 {i+1} 素材定位失败，已回退程序动画。")
                                page.evaluate("() => window.loadMedia('', '')")
                                page.evaluate("a => window.renderFrame(...a)", args)
                            proc.stdin.write(page.screenshot(type="jpeg", quality=90, animations="disabled"))
                        proc.stdin.close()
                        err = proc.stderr.read()
                        if proc.wait() != 0:
                            raise RuntimeError("编码失败：" + err.decode("utf-8", "replace")[-400:])
                    except Exception:
                        proc.kill(); proc.wait(); raise
                manifest["scenes"].append({"fingerprint": fingerprint if cacheable else "", "voice_key": [scene["narration"], voice_provider, voice_name, voice_speed] if voice else None})
                clips.append(clip)
                parts = split_text(scene["narration"])
                for j, part in enumerate(parts):
                    start = elapsed + speech_duration*j/len(parts)
                    end = elapsed + (duration if j == len(parts)-1 else speech_duration*(j+1)/len(parts))
                    captions.append(f"{len(captions)+1}\n{stamp(start)} --> {stamp(end)}\n{part}\n")
                elapsed += duration
        finally:
            browser.close()
    update("正在合成画面、配音与音乐")
    (outdir / "concat.txt").write_text("".join("file '"+p.name+"'\n" for p in clips), encoding="utf-8")
    base = outdir / "combined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(outdir / "concat.txt"), "-c", "copy", str(base)])
    output = outdir / "video.mp4"
    if music:
        fade_start = max(0, elapsed - 1.0)
        music_filter = f"[1:a]volume={music_volume},apad,atrim=duration={elapsed},afade=t=out:st={fade_start}:d=1[bg]"
        if voice:
            filt = music_filter + ";[0:a][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
        else:
            filt = music_filter + ";[bg]anull[a]"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(base), "-i", str(music), "-filter_complex", filt, "-map", "0:v", "-map", "[a]", "-t", str(elapsed), "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(output)])
    else:
        base.replace(output)
    (outdir / "subtitles.srt").write_text("\n".join(captions), encoding="utf-8")
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return round(elapsed, 2)
