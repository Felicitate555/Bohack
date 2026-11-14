"""Deterministic, CPU-only motion graphics; all text is data, never executable code."""
import asyncio
import math
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from .services import speech
from .providers.siliconflow import ProviderError
from .scene_spec import distinct_probability, binary_search_steps
from . import style_profile, sound_effects

W, H, FPS = 960, 540, 12
GOLD = (219, 185, 120)
WHITE = (239, 232, 213)
MUTED = (148, 139, 118)


class RenderCancelled(Exception):
    """A user-requested cooperative render stop."""


@lru_cache(maxsize=64)
def font(size, family="serif"):
    serif = ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "C:/Windows/Fonts/simsun.ttc"]
    sans = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/msyh.ttc", "/System/Library/Fonts/PingFang.ttc"]
    candidates = [os.getenv("VIBELAB_FONT", "")] + (sans + serif if family == "sans" else serif + sans)
    for path in candidates:
        if path and Path(path).exists():
            try: return ImageFont.truetype(path, size)
            except OSError: continue
    raise RuntimeError("未找到中文字体，请设置 VIBELAB_FONT。")


def text_fit(draw, xy, value, size, color, max_width, anchor=None, family="serif"):
    value = str(value)
    while size > 13 and draw.textbbox((0, 0), value, font=font(size, family))[2] > max_width:
        size -= 1
    draw.text(xy, value, font=font(size, family), fill=color, anchor=anchor)


def split_text(value, width=27):
    lines = []
    while value:
        part = value[:width]
        if len(value) > width:
            candidates = [part.rfind(c) for c in "，。！？；、"]
            cut = max(candidates)
            if cut > width // 2:
                part = value[:cut + 1]
        lines.append(part)
        value = value[len(part):]
    return lines


def network_geometry(scene, t, duration):
    spec = scene.get("spec") or {}
    params, animation, layout = spec.get("parameters") or {}, spec.get("animation") or {}, spec.get("layout") or {}
    count = params.get("count") or 8
    points = []
    for i in range(count):
        angle, radius = i * math.tau / count - math.pi / 2, 110 + (i % 3) * 10
        points.append([layout.get("graphic_x", 650) + math.cos(angle) * radius,
                       layout.get("graphic_y", 255) + math.sin(angle) * radius])
    for motion in animation.get("node_motions", []):
        node = motion["node"]
        if node >= len(points): continue
        start, end = motion["start"] * duration, motion["end"] * duration
        p = max(0, min(1, (t - start) / max(.001, end - start)))
        if motion.get("easing") != "linear": p = 1 - (1 - p) ** 3
        dx = motion["from_x"] + (motion["to_x"] - motion["from_x"]) * p
        dy = motion["from_y"] + (motion["to_y"] - motion["from_y"]) * p
        points[node] = [max(50, min(910, points[node][0] + dx)), max(130, min(420, points[node][1] + dy))]
    keyframes = animation.get("keyframes") or [0, .65]
    raw = max(0, min(1, (t / max(duration, .001) - keyframes[0]) / max(.01, keyframes[1] - keyframes[0])))
    motion = animation.get("motion")
    p = raw if motion == "linear" else math.floor(raw * 4) / 4 if motion == "step" else 1 - (1 - raw) ** 3
    edges = params.get("edges") or [[i, j] for i in range(count) for j in range(i + 1, count)]
    return {"points": points, "edges": edges, "shown": math.floor(p * len(edges)), "visible": max(1, math.ceil(p * count))}


def frame(scene, index, count, t, duration, profile=None):
    style = style_profile.normalize(profile)
    palette, typography = style["palette"], style["typography"]
    def rgb(value): return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
    GOLD, WHITE, MUTED = (rgb(palette[key]) for key in ("accent", "text", "muted"))
    family = typography["family"]
    margin = round(W * style["layout"]["safe_margin"])
    def styled_text(*args, **kwargs):
        number = re.fullmatch(r"[\d\s.,%+−×÷=≈/\-]+", str(args[2])) is not None
        return text_fit(*args, **kwargs, family=typography["number_family"] if number else family)
    im = Image.new("RGB", (W, H), rgb(palette["background"]))
    d = ImageDraw.Draw(im)
    # Subtle fixed grid and particles preserve the restrained reference aesthetic.
    for x in range(0, W, 96):
        d.line((x, 0, x, H), fill=rgb(palette["surface"]))
    background = rgb(palette["background"])
    def tint(amount):
        return tuple(round(base * (1-amount) + accent * amount) for base, accent in zip(background, GOLD))
    grain = style["effects"]["grain"]
    dust = tuple(round(base * (1 - grain) + accent * grain) for base, accent in zip(background, GOLD))
    for i in range(54):
        x = (i * 157 + 83) % W
        y = (i * 83 + 41) % 420
        d.ellipse((x, y, x + 1, y + 1), fill=dust)
    d.text((margin, 28), f"{index + 1:02}  /  {count:02}     V I B E L A B", font=font(14, family), fill=MUTED)
    d.line((margin, 54, W-margin, 54), fill=MUTED, width=1)
    styled_text(d, (margin, 77), scene["heading"], typography["title_size"], WHITE, W-margin*2)
    progress = min(1.0, max(0.0, t / max(1, duration * .6)))
    animation = (scene.get("spec") or {}).get("animation") or {}
    keyframes = animation.get("keyframes") or [0, .65]
    if keyframes != [0, .65]:
        raw = max(0.0, min(1.0, (t / max(duration, .001) - keyframes[0]) / max(.01, keyframes[1] - keyframes[0])))
        motion = animation.get("motion")
        progress = raw if motion == "linear" else math.floor(raw * 4) / 4 if motion == "step" else 1 - (1 - raw) ** 3
    visual = scene["visual"]
    if visual in ("dots", "network"):
        graph = network_geometry(scene, t, duration)
        pts = graph["points"]
        if visual == "network":
            for a, b in graph["edges"][:graph["shown"]]:
                d.line((*pts[a], *pts[b]), fill=tint(.4), width=1)
        visible = len(pts) if visual == "network" else graph["visible"]
        for x, y in pts[:visible]:
            r = 3
            d.ellipse((x-r, y-r, x+r, y+r), fill=GOLD)
        styled_text(d, (95, 190), scene["key"], 60, GOLD, 380)
        styled_text(d, (95, 266), scene["detail"], 19, MUTED, 395)
        if visual == "network" and (scene.get("spec", {}).get("parameters") or {}).get("count"):
            d.text((95, 303), f"连线 {graph['shown']} / {len(graph['edges'])}", font=font(18, family), fill=GOLD)
    elif visual == "binary_search":
        values = [e["value"] for e in scene["spec"].get("elements", [])]
        target = scene["spec"]["parameters"].get("target")
        try:
            if scene["spec"]["parameters"].get("search_invalid"): raise ValueError("invalid search values")
            steps = binary_search_steps(values, target)
        except ValueError:
            styled_text(d, (480, 250), "请填写递增数列和查找目标", 22, MUTED, 800, "mm")
        else:
            step = steps[min(len(steps)-1, int(progress*len(steps)))]
            styled_text(d, (480, 165), f"查找目标 {target:g}", 30, GOLD, 800, "mm")
            width = min(86, 720/len(values)); left = (W-width*len(values))/2
            for j,value in enumerate(values):
                active = step["left"] <= j <= step["right"]; color = GOLD if j == step["mid"] else WHITE if active else MUTED
                x = left+j*width
                d.rounded_rectangle((x+4,215,x+width-4,275),radius=6,outline=color,width=3 if j==step["mid"] else 1)
                styled_text(d,(x+width/2,245),f"{value:g}",24,color,width-12,"mm")
                styled_text(d,(x+width/2,298),str(j+1),14,MUTED,width-8,"mm")
            result = step["result"]
            label = "未找到目标值" if result == -1 else f"找到：第 {result+1} 项" if result is not None else f"{step['value']:g} {'<' if step['comparison']=='less' else '>'} {target:g}，继续查找{'右半区' if step['comparison']=='less' else '左半区'}"
            styled_text(d,(480,355),label,25,GOLD,820,"mm")
            styled_text(d,(480,400),"已排除的区间逐步变暗",16,MUTED,820,"mm")
    elif visual == "compare":
        styled_text(d, (140, 185), scene["key"], 76, GOLD, 680)
        params = (scene.get("spec") or {}).get("parameters") or {}
        value = params.get("value")
        d.rounded_rectangle((145, 310, 820, 325), radius=7, fill=tint(.22))
        ratio = value / 100 if value is not None else 0
        d.rounded_rectangle((145, 310, 145 + max(1, int(675 * progress * ratio)), 325), radius=7, fill=GOLD)
        if value is not None: styled_text(d, (145, 270), f"{value:g}%", 20, WHITE, 260)
    elif visual == "formula":
        styled_text(d, (92, 175), scene["detail"], 32, WHITE, 780)
        d.line((95, 253, 95 + int(700 * progress), 253), fill=GOLD, width=2)
        styled_text(d, (95, 285), scene["key"], 57, GOLD, 730)
    elif visual == "distinct":
        params = (scene.get("spec") or {}).get("parameters") or {}
        categories, draws = params.get("categories"), params.get("draws")
        if not categories or not draws or draws > categories + 1:
            styled_text(d, (100, 220), "请填写有效的类别数和抽取次数", 24, WHITE, 760)
        else:
            step = min(draws, max(1, int(progress * draws) + 1))
            prob, prior = distinct_probability(categories, step), distinct_probability(categories, step - 1)
            styled_text(d, (100, 137), f"类别 {categories} · 抽取 {draws} 次", 18, MUTED, 370)
            styled_text(d, (100, 181), f"前 {step-1} 次均不同 × 第 {step} 次仍不同", 16, WHITE, 370)
            styled_text(d, (100, 215), f"{prior*100:.2f}% × {max(0,categories-step+1)} / {categories}", 27, GOLD, 370)
            styled_text(d, (100, 275), f"全部不同 ≈ {prob*100:.2f}%", 24, GOLD, 370)
            styled_text(d, (100, 315), f"至少一次重复 ≈ {(1-prob)*100:.2f}%", 19, WHITE, 370)
            d.line((540, 175, 540, 335, 840, 335), fill=tint(.5), width=1)
            points = [(540 + 300 * (i-1) / max(1, draws-1), 335 - distinct_probability(categories, i) * 160) for i in range(1, step+1)]
            if len(points) > 1: d.line(points, fill=GOLD, width=3)
            x, y = points[-1]; d.ellipse((x-4, y-4, x+4, y+4), fill=GOLD)
            styled_text(d, (100, 395), "独立、等概率抽取（允许重复）", 13, MUTED, 760)
    elif visual == "flow":
        params = (scene.get("spec") or {}).get("parameters") or {}
        labels = (params.get("labels") or ["01", "02", "03"])[:5]
        left, right, y = 170, 790, 255
        d.line((left, y, left + (right-left) * progress, y), fill=tint(.65), width=2)
        for i, label in enumerate(labels):
            x = left + (right-left) * i / max(1, len(labels)-1)
            if progress >= i / max(1, len(labels)-1):
                d.ellipse((x-7, y-7, x+7, y+7), fill=GOLD)
                styled_text(d, (x, 305), label, 17, WHITE, 125, "mm")
        styled_text(d, (480, 180), scene["key"], 43, GOLD, 700, "mm")
    elif visual == "question":
        params = (scene.get("spec") or {}).get("parameters") or {}
        candidates = params.get("candidates") or []
        delay = (scene.get("spec") or {}).get("timing", {}).get("narration_delay", 0)
        if candidates and len(candidates) > 1 and animation.get("reveal_on_narration") and delay > 0:
            choice = len(candidates)-1 if t >= delay else min(len(candidates)-2, int((t / delay) * (len(candidates)-1)))
        else:
            choice = min(len(candidates)-1, int(progress*len(candidates))) if candidates else 0
        shown = candidates[choice] if candidates else scene["key"]
        styled_text(d, (480, 242), shown, 70, GOLD, 800, "mm")
        d.line((350, 314, 610, 314), fill=tint(.42), width=1)
    else:
        size = int(65 + 8 * progress)
        styled_text(d, (480, 242), scene["key"], size, GOLD, 800, "mm")
        d.line((350, 314, 610, 314), fill=tint(.42), width=1)
    if visual not in {"formula", "distinct", "dots", "network", "binary_search"}:
        styled_text(d, (480, 360), scene["detail"], 20, MUTED, 800, "mm")
    # Sentence-level subtitle timing uses the actual scene duration.
    timing = scene.get("spec", {}).get("timing", {})
    speech_duration = timing.get("speech_duration", duration)
    captions = list(caption_timeline(scene["narration"], duration, speech_duration, timing.get("caption_cues"), timing.get("narration_delay", 0)))
    caption = next((part for part, start, end in captions if start <= t < end), "")
    styled_text(d, (480, style["layout"]["subtitle_y"]-7), caption, typography["subtitle_size"], WHITE, W-margin*2, "mm")
    d.line((margin, 510, margin + int((W-margin*2) * ((index + min(t / duration, 1)) / count)), 510), fill=GOLD, width=2)
    return im


def run(args, **kwargs):
    try:
        return subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kwargs)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("音视频处理失败：" + exc.stderr.decode("utf-8", "replace")[-500:]) from exc


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def caption_ranges(parts, duration, speech_duration):
    weights = [max(1, len(p.strip())) for p in parts]
    total = sum(weights)
    elapsed = 0
    for i, weight in enumerate(weights):
        start = speech_duration * elapsed / total
        elapsed += weight
        end = duration if i == len(weights) - 1 else speech_duration * elapsed / total
        yield start, end


def scene_narration_delay(scene):
    from .scene_spec import bounded
    animation = (scene.get("spec") or {}).get("animation") or {}
    return bounded(animation.get("narration_delay"), 0, 2) or 0


def caption_timeline(text, duration, speech_duration, cues=None, delay=0):
    delay = max(0, min(delay, duration))
    if cues:
        for i, cue in enumerate(cues):
            end = cues[i+1]['start'] + delay if i+1 < len(cues) else duration
            yield cue['text'], cue['start'] + delay, min(duration, end)
    else:
        parts = split_text(text)
        for part, (start, end) in zip(parts, caption_ranges(parts, duration - delay, min(speech_duration, duration - delay))):
            yield part, start + delay, min(duration, end + delay)


def render(project, outdir, voice, update, target_seconds=None, voice_provider="edge", voice_name="anna", voice_speed=1.0, report=lambda event: None, narration_mode="scene"):
    check_cancel = getattr(update, 'check_cancel', lambda: None)
    check_cancel()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("未找到 FFmpeg/FFprobe，请安装后重试。")
    outdir.mkdir(parents=True, exist_ok=True)
    scenes = project["scenes"]
    profile = style_profile.normalize(project.get("style_profile"))
    instruction = speech.narration_instruction(voice_provider, profile) if voice else None
    voice_durations, voice_cues = [], []
    if voice:
        try:
            if narration_mode == "sentence":
                speech.create_segmented(scenes, outdir, voice_provider, voice_name, voice_speed, update, report, cache_dir=outdir.parent / "speech_cache", instruction=instruction)
            else:
                if instruction:
                    speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report, instruction=instruction)
                else:
                    speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report)
            for i in range(len(scenes)):
                result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(outdir / f"voice_{i}.mp3")], capture_output=True, text=True, check=True, timeout=20)
                voice_durations.append(float(result.stdout.strip()))
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
    extension = max(0, (target_seconds or 0) - sum(minimums)) / len(scenes)
    clips, captions, elapsed = [], [], 0.0
    has_effects = any((s.get("spec", {}).get("animation") or {}).get("sound_effect") == "soft_reveal" for s in scenes)
    for i, scene in enumerate(scenes):
        update(f"正在制作镜头 {i+1}/{len(scenes)}：{scene['heading']}")
        audio = outdir / f"voice_{i}.mp3"
        duration = minimums[i] + extension
        frames = max(1, math.ceil(duration * FPS))
        duration = frames / FPS
        speech_duration = min(duration - delays[i], voice_durations[i]) if voice else duration - delays[i]
        scene.setdefault("spec", {})["timing"] = {"start": round(elapsed, 3), "end": round(elapsed + duration, 3), "speech_duration": round(speech_duration, 3), "narration_delay": delays[i]}
        if voice and voice_cues[i]: scene["spec"]["timing"]["caption_cues"] = voice_cues[i]
        clip = outdir / f"scene_{i}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0"]
        effect_time = sound_effects.frame_reveal_time(scene, duration, [], FPS, 'light') if (scene.get("spec", {}).get("animation") or {}).get("sound_effect") == "soft_reveal" else None
        if effect_time is not None: scene["spec"]["timing"]["sound_effect_time"] = effect_time
        effect = sound_effects.write_track([scene], [duration], outdir / f"effect_{i}.wav", [effect_time]) if has_effects else None
        if voice:
            cmd += ["-i", str(audio)]
        if effect:
            cmd += ["-i", str(effect)]
        elif has_effects and not voice:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
        if voice and effect:
            cmd += ["-filter_complex", f"[1:a]adelay={round(delays[i]*1000)}:all=1,apad[v];[v][2:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]", "-map", "0:v", "-map", "[a]"]
        elif voice:
            cmd += ["-af", f"adelay={round(delays[i]*1000)}:all=1,apad"]
        elif has_effects:
            cmd += ["-af", "apad"]
        if voice or has_effects:
            cmd += ["-c:a", "aac", "-b:a", "96k"]
            if has_effects:
                cmd += ["-ar", "44100", "-ac", "1"]
        cmd += ["-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", str(clip)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for f in range(frames):
                check_cancel()
                proc.stdin.write(frame(scene, i, len(scenes), f/FPS, duration, profile).tobytes())
            proc.stdin.close()
            err = proc.stderr.read()
            if proc.wait() != 0:
                raise RuntimeError("镜头编码失败：" + err.decode("utf-8", "replace")[-500:])
        except Exception:
            proc.kill()
            proc.wait()
            raise
        finally:
            if proc.stderr:
                proc.stderr.close()
        clips.append(clip)
        for part, a, b in caption_timeline(scene["narration"], duration, speech_duration, voice_cues[i] if voice else None, delays[i]):
            a, b = elapsed + a, elapsed + b
            captions.append(f"{len(captions)+1}\n{stamp(a)} --> {stamp(b)}\n{part}\n")
        elapsed += duration
    update("正在合成视频与字幕")
    concat = outdir / "concat.txt"
    concat.write_text("".join("file '" + p.name + "'\n" for p in clips), encoding="utf-8")
    output = outdir / "video.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(output)])
    check_cancel()
    (outdir / "subtitles.srt").write_text("\n".join(captions), encoding="utf-8")
    return round(elapsed, 2)
