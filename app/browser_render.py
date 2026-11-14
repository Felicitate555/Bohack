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

from .render import run, stamp, caption_timeline, scene_narration_delay, RenderCancelled
from .services import speech
from .providers.siliconflow import ProviderError
from . import style_profile, sound_effects


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


def music_mix_filter(duration, volume, voice, voice_input="0:a", music_input="1:a"):
    fade_start = max(0, duration - 1.0)
    music = f"[{music_input}]volume={volume},apad,atrim=duration={duration},afade=t=out:st={fade_start}:d=1[bg]"
    if not voice:
        return music + ";[bg]anull[a]"
    return (music + f";[{voice_input}]asplit=2[voice][side];"
            "[bg][side]sidechaincompress=threshold=0.025:ratio=6:attack=20:release=250[ducked];"
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")


def mux_audio(base, output, voices, durations, music, music_volume, delays=None, effects=None):
    """Encode narration once after joining sample-aligned per-scene audio."""
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(base)]
    for voice in voices:
        command += ["-i", str(voice)]
    if music:
        command += ["-i", str(music)]
    if effects:
        command += ["-i", str(effects)]
    filters = []
    for i, duration in enumerate(durations if voices else []):
        delay = round((delays[i] if delays else 0) * 1000)
        filters.append(f"[{i+1}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={delay}:all=1,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[v{i}]")
    if voices:
        filters.append("".join(f"[v{i}]" for i in range(len(voices))) + f"concat=n={len(voices)}:v=0:a=1[narr]")
    total = sum(durations)
    if music:
        filters.append(music_mix_filter(total, music_volume, bool(voices), "narr", f"{len(voices)+1}:a").replace("[a]", "[premix]"))
    elif voices:
        filters.append("[narr]anull[premix]")
    else:
        filters.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={total}[premix]")
    if effects:
        effect_input = len(voices) + (1 if music else 0) + 1
        filters.append(f"[premix][{effect_input}:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    else:
        filters.append("[premix]anull[a]")
    command += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[a]", "-t", str(total), "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(output)]
    run(command)


def usable_beats(timeline):
    timeline = timeline or {}
    if timeline.get("rhythm_status") != "estimated":
        return [], []
    return timeline.get("beats", []), timeline.get("strong_beats", [])


def render(project, outdir, voice, update, quality="preview", music=None, beat_timeline=None, music_volume=.18, voice_provider="edge", voice_name="anna", voice_speed=1.0, report=lambda event: None, previous_run=None, narration_mode="scene"):
    check_cancel = getattr(update, 'check_cancel', lambda: None)
    check_cancel()
    if not available():
        raise RuntimeError("高质量渲染需要安装 Playwright 和 Chromium；请使用 Docker 启动或选择轻量渲染。")
    from playwright.sync_api import sync_playwright
    fps = 30 if quality == "full" else 15
    w, h = (1920, 1080) if quality == "full" else (960, 540)
    scene_html = (Path(__file__).parent / "scene_canvas.html").read_text(encoding="utf-8")
    profile = style_profile.normalize(project.get("style_profile"))
    instruction = speech.narration_instruction(voice_provider, profile) if voice else None
    renderer_fingerprint = hashlib.sha256(scene_html.encode("utf-8")).hexdigest()
    outdir.mkdir(parents=True, exist_ok=True)
    scenes = project["scenes"]
    clips, captions, durations, effect_times, elapsed = [], [], [], [], 0.0
    previous_dir = outdir.parent / previous_run if previous_run and len(previous_run) == 12 and all(c in '0123456789abcdef' for c in previous_run) else None
    old_manifest = {}
    if previous_dir:
        try:
            old_manifest = json.loads((previous_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    old_scenes = old_manifest.get("scenes", [])
    manifest = {"version": 1, "scenes": []}
    beats, strong_beats = usable_beats(beat_timeline)
    # The synchronous Playwright API runs an event loop in this thread. Produce
    # narration beforehand so asyncio.run() never nests inside that loop.
    voice_durations, voice_cues = [], []
    if voice:
        try:
            pending = []
            for i, scene in enumerate(scenes):
                old = old_scenes[i] if i < len(old_scenes) else {}
                voice_key = speech.voice_cache_key(scene["narration"], voice_provider, voice_name, voice_speed, narration_mode, instruction)
                source = previous_dir / f"voice_{i}.mp3" if previous_dir else None
                cue_source = previous_dir / f"voice_{i}.cues.json" if previous_dir else None
                valid_cues = True
                if narration_mode == "sentence":
                    if cue_source is None:
                        valid_cues = False
                    else:
                        try: speech.read_cues(cue_source, scene["narration"])
                        except ValueError: valid_cues = False
                if old.get("voice_key") == voice_key and source and source.is_file() and valid_cues:
                    shutil.copyfile(source, outdir / f"voice_{i}.mp3")
                    if narration_mode == "sentence": shutil.copyfile(cue_source, outdir / f"voice_{i}.cues.json")
                else:
                    pending.append(i)
            if pending:
                if narration_mode == "sentence":
                    speech.create_segmented(scenes, outdir, voice_provider, voice_name, voice_speed, update, report, pending, cache_dir=outdir.parent / "speech_cache", instruction=instruction)
                else:
                    if instruction:
                        speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report, pending, instruction=instruction)
                    else:
                        speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report, pending)
            check_cancel()
            for i in range(len(scenes)):
                info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(outdir / f"voice_{i}.mp3")], capture_output=True, text=True, check=True, timeout=20)
                voice_durations.append(float(info.stdout.strip()))
                voice_cues.append(speech.read_cues(outdir / f"voice_{i}.cues.json", scenes[i]["narration"], voice_durations[-1]) if narration_mode == "sentence" else None)
        except RenderCancelled:
            raise
        except ProviderError as exc:
            raise RuntimeError(str(exc)) from exc
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            raise RuntimeError("配音生成或音频解析失败。请检查所选配音服务的网络连接和 FFprobe，或更换配音服务。") from exc
    delays = [scene_narration_delay(scene) for scene in scenes]
    minimums = [max(3.0, voice_durations[i] + .6 + delays[i]) if voice else max(4.0, min(12.0, len(s["narration"]) / 4.2 + .8) + delays[i]) for i, s in enumerate(scenes)]
    extension = max(0, (project.get("target_seconds") or 0) - sum(minimums)) / len(scenes)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path(), headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.set_content(scene_html, wait_until="load")
            page.evaluate("style => window.setStyle(style)", profile)
            page.evaluate("document.fonts.ready")
            for i, scene in enumerate(scenes):
                update(f"正在制作镜头 {i+1}/{len(scenes)}：{scene['heading']}")
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
                effect_time = sound_effects.frame_reveal_time(scene, duration, local_beats, fps, 'browser') if scene['spec']['animation'].get('sound_effect') == 'soft_reveal' else None
                effect_times.append(effect_time)
                speech_duration = min(duration - delays[i], voice_durations[i]) if voice else duration - delays[i]
                scene["spec"]["timing"] = {"start": round(elapsed, 3), "end": round(elapsed+duration, 3), "speech_duration": round(speech_duration, 3), "narration_delay": delays[i]}
                if voice and voice_cues[i]: scene["spec"]["timing"]["caption_cues"] = voice_cues[i]
                if effect_time is not None: scene["spec"]["timing"]["sound_effect_time"] = effect_time
                hash_scene = dict(scene)
                hash_scene["spec"] = {k:v for k,v in scene["spec"].items() if k != "timing"}
                hash_input = {"clip_format":2, "renderer":renderer_fingerprint, "profile":profile, "scene":hash_scene, "index":i, "count":len(scenes), "duration":duration, "beats":local_beats, "strong":local_strong, "fps":fps, "width":w, "height":h, "voice":[voice,voice_provider,voice_name,voice_speed,narration_mode], "cues":voice_cues[i] if voice else None}
                fingerprint = hashlib.sha256(json.dumps(hash_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                old = old_scenes[i] if i < len(old_scenes) else {}
                prior_clip = previous_dir / f"scene_{i}.mp4" if previous_dir else None
                media = scene["spec"].get("media") or {}
                asset_id, kind = media.get("asset_id"), media.get("kind")
                source = outdir.parent / "assets" / (str(asset_id) + (".mp4" if kind == "video" else ".png")) if asset_id and kind in {"image", "video"} else None
                cacheable = not source or source.is_file()
                media_loaded = False
                if source and source.is_file():
                    mime = "video/mp4" if kind == "video" else "image/png"
                    payload = "data:" + mime + ";base64," + base64.b64encode(source.read_bytes()).decode("ascii")
                    try:
                        page.evaluate("a => window.loadMedia(a[0],a[1])", [kind, payload])
                        media_loaded = True
                    except Exception:
                        cacheable = False
                        project.setdefault("warnings", []).append(f"镜头 {i+1} 素材加载失败，已回退程序动画。")
                        page.evaluate("() => window.loadMedia('', '')")
                else:
                    page.evaluate("() => window.loadMedia('', '')")
                clip = outdir / f"scene_{i}.mp4"
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-vcodec", "mjpeg", "-i", "pipe:0"]
                cmd += ["-t", str(duration), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip)]
                if cacheable and old.get("fingerprint") == fingerprint and prior_clip and prior_clip.is_file():
                    update(f"复用未修改的镜头 {i+1}/{len(scenes)}")
                    shutil.copyfile(prior_clip, clip)
                else:
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    try:
                        for frame in range(frames):
                            check_cancel()
                            args = [scene, i, len(scenes), frame/fps, duration, local_beats, local_strong, w, h]
                            try:
                                page.evaluate("a => window.renderFrame(...a)", args)
                            except Exception as exc:
                                if not media_loaded:
                                    raise RuntimeError(f"镜头 {i+1} 的程序动画绘制失败，请检查场景数据和渲染环境。") from exc
                                cacheable = False
                                project.setdefault("warnings", []).append(f"镜头 {i+1} 素材定位失败，已回退程序动画。")
                                page.evaluate("() => window.loadMedia('', '')")
                                media_loaded = False
                                page.evaluate("a => window.renderFrame(...a)", args)
                            proc.stdin.write(page.screenshot(type="jpeg", quality=90, animations="disabled"))
                        proc.stdin.close()
                        err = proc.stderr.read()
                        if proc.wait() != 0:
                            raise RuntimeError("编码失败：" + err.decode("utf-8", "replace")[-400:])
                    except Exception:
                        proc.kill(); proc.wait(); raise
                manifest["scenes"].append({"fingerprint": fingerprint if cacheable else "", "voice_key": speech.voice_cache_key(scene["narration"], voice_provider, voice_name, voice_speed, narration_mode, instruction) if voice else None})
                clips.append(clip)
                durations.append(duration)
                for part, start, end in caption_timeline(scene["narration"], duration, speech_duration, voice_cues[i] if voice else None, delays[i]):
                    start, end = elapsed + start, elapsed + end
                    captions.append(f"{len(captions)+1}\n{stamp(start)} --> {stamp(end)}\n{part}\n")
                elapsed += duration
        finally:
            browser.close()
    update("正在合成画面、配音与音乐")
    (outdir / "concat.txt").write_text("".join("file '"+p.name+"'\n" for p in clips), encoding="utf-8")
    base = outdir / "combined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(outdir / "concat.txt"), "-c", "copy", str(base)])
    output = outdir / "video.mp4"
    effects = sound_effects.write_track(scenes, durations, outdir / "effects.wav", effect_times)
    if voice or music or effects:
        mux_audio(base, output, [outdir / f"voice_{i}.mp3" for i in range(len(scenes))] if voice else [], durations, music, music_volume, delays, effects)
    else:
        base.replace(output)
    check_cancel()
    (outdir / "subtitles.srt").write_text("\n".join(captions), encoding="utf-8")
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return round(elapsed, 2)
