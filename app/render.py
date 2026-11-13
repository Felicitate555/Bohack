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

W, H, FPS = 960, 540, 12
GOLD = (219, 185, 120)
WHITE = (239, 232, 213)
MUTED = (148, 139, 118)


@lru_cache(maxsize=32)
def font(size):
    candidates = [os.getenv("VIBELAB_FONT", ""), "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/msyh.ttc", "/System/Library/Fonts/PingFang.ttc"]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("未找到中文字体，请设置 VIBELAB_FONT。")


def text_fit(draw, xy, value, size, color, max_width, anchor=None):
    value = str(value)
    while size > 13 and draw.textbbox((0, 0), value, font=font(size))[2] > max_width:
        size -= 1
    draw.text(xy, value, font=font(size), fill=color, anchor=anchor)


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


def frame(scene, index, count, t, duration):
    im = Image.new("RGB", (W, H), (11, 11, 12))
    d = ImageDraw.Draw(im)
    # Subtle fixed grid and particles preserve the restrained reference aesthetic.
    for x in range(0, W, 96):
        d.line((x, 0, x, H), fill=(18, 18, 18))
    for i in range(54):
        x = (i * 157 + 83) % W
        y = (i * 83 + 41) % 420
        d.ellipse((x, y, x + 1, y + 1), fill=(55, 49, 37))
    d.text((48, 28), f"{index + 1:02}  /  {count:02}     V I B E L A B", font=font(14), fill=MUTED)
    d.line((48, 54, 912, 54), fill=(79, 69, 51), width=1)
    text_fit(d, (48, 77), scene["heading"], 32, WHITE, 820)
    progress = min(1.0, max(0.0, t / max(1, duration * .6)))
    visual = scene["visual"]
    if visual in ("dots", "network"):
        pts = [(620 + math.cos(2 * math.pi * i / 23) * (75 + (i % 3) * 20), 247 + math.sin(2 * math.pi * i / 23) * (75 + (i % 3) * 20)) for i in range(23)]
        n = max(1, int(progress * 23))
        if visual == "network":
            for j in range(1, n):
                d.line((*pts[0], *pts[j]), fill=(91, 77, 51), width=1)
        for j, (x, y) in enumerate(pts[:n]):
            r = 5 if j == 0 else 3
            d.ellipse((x-r, y-r, x+r, y+r), fill=GOLD)
        text_fit(d, (130, 198), scene["key"], 76, GOLD, 350)
    elif visual == "compare":
        text_fit(d, (140, 185), scene["key"], 76, GOLD, 680)
        d.rounded_rectangle((145, 310, 820, 325), radius=7, fill=(60, 55, 45))
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", scene["key"])
        ratio = min(float(match.group(1)) / 100, 1) if match else 1
        d.rounded_rectangle((145, 310, 145 + max(1, int(675 * progress * ratio)), 325), radius=7, fill=GOLD)
    elif visual == "formula":
        text_fit(d, (92, 175), scene["detail"], 32, WHITE, 780)
        d.line((95, 253, 95 + int(700 * progress), 253), fill=GOLD, width=2)
        text_fit(d, (95, 285), scene["key"], 57, GOLD, 730)
    elif visual == "flow":
        for i in range(3):
            x = 220 + i * 245
            color = GOLD if progress > i / 4 else (72, 65, 52)
            d.ellipse((x-28, 220, x+28, 276), outline=color, width=2)
            d.text((x, 248), f"0{i+1}", font=font(19), fill=color, anchor="mm")
            if i < 2:
                d.line((x+29, 248, x+216, 248), fill=(95, 80, 52), width=2)
        text_fit(d, (480, 320), scene["key"], 46, GOLD, 700, "mm")
    else:
        size = int(65 + 8 * progress)
        text_fit(d, (480, 242), scene["key"], size, GOLD, 800, "mm")
        d.line((350, 314, 610, 314), fill=(95, 80, 52), width=1)
    if visual != "formula":
        text_fit(d, (480, 360), scene["detail"], 20, MUTED, 800, "mm")
    # Sentence-level subtitle timing uses the actual scene duration.
    lines = split_text(scene["narration"])
    speech_duration = scene.get("spec", {}).get("timing", {}).get("speech_duration", duration)
    caption = lines[min(len(lines)-1, int(t / max(speech_duration, .1) * len(lines)))]
    text_fit(d, (480, 463), caption, 22, WHITE, 850, "mm")
    d.line((48, 510, 48 + int(864 * ((index + min(t / duration, 1)) / count)), 510), fill=GOLD, width=2)
    return im


def run(args, **kwargs):
    try:
        return subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kwargs)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("音视频处理失败：" + exc.stderr.decode("utf-8", "replace")[-500:]) from exc


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def render(project, outdir, voice, update, target_seconds=None, voice_provider="edge", voice_name="anna", voice_speed=1.0, report=lambda event: None):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("未找到 FFmpeg/FFprobe，请安装后重试。")
    outdir.mkdir(parents=True, exist_ok=True)
    scenes = project["scenes"]
    voice_durations = []
    if voice:
        try:
            speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report)
            for i in range(len(scenes)):
                result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(outdir / f"voice_{i}.mp3")], capture_output=True, text=True, check=True, timeout=20)
                voice_durations.append(float(result.stdout.strip()))
        except Exception as exc:
            raise RuntimeError("语音生成失败：请检查网络或选择无配音模式。") from exc
    minimums = [max(3.0, voice_durations[i] + .6) if voice else max(4.0, min(12.0, len(s["narration"]) / 4.2 + .8)) for i, s in enumerate(scenes)]
    extension = max(0, (target_seconds or 0) - sum(minimums)) / len(scenes)
    clips, captions, elapsed = [], [], 0.0
    for i, scene in enumerate(scenes):
        update(f"正在制作镜头 {i+1}/{len(scenes)}：{scene['heading']}")
        audio = outdir / f"voice_{i}.mp3"
        duration = minimums[i] + extension
        frames = max(1, math.ceil(duration * FPS))
        duration = frames / FPS
        speech_duration = min(duration, voice_durations[i]) if voice else duration
        scene.setdefault("spec", {})["timing"] = {"start": round(elapsed, 3), "end": round(elapsed + duration, 3), "speech_duration": round(speech_duration, 3)}
        clip = outdir / f"scene_{i}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0"]
        if voice:
            cmd += ["-i", str(audio), "-af", "apad", "-c:a", "aac", "-b:a", "96k"]
        cmd += ["-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", str(clip)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for f in range(frames):
                proc.stdin.write(frame(scene, i, len(scenes), f/FPS, duration).tobytes())
            proc.stdin.close()
            err = proc.stderr.read()
            if proc.wait() != 0:
                raise RuntimeError("镜头编码失败：" + err.decode("utf-8", "replace")[-500:])
        except Exception:
            proc.kill()
            proc.wait()
            raise
        clips.append(clip)
        parts = split_text(scene["narration"])
        for j, part in enumerate(parts):
            a = elapsed + speech_duration*j/len(parts)
            b = elapsed + (duration if j == len(parts)-1 else speech_duration*(j+1)/len(parts))
            captions.append(f"{len(captions)+1}\n{stamp(a)} --> {stamp(b)}\n{part}\n")
        elapsed += duration
    update("正在合成视频与字幕")
    concat = outdir / "concat.txt"
    concat.write_text("".join("file '" + p.name + "'\n" for p in clips), encoding="utf-8")
    output = outdir / "video.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(output)])
    (outdir / "subtitles.srt").write_text("\n".join(captions), encoding="utf-8")
    return round(elapsed, 2)
