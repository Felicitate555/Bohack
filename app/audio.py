"""Lightweight onset and tempo estimation from decoded mono audio."""
import json
import math
import subprocess

import numpy as np


def analyze(path):
    try:
        info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, check=True, timeout=15)
        duration = float(json.loads(info.stdout)["format"]["duration"])
        if not 1 <= duration <= 300:
            raise ValueError("音乐长度须在 1 秒至 5 分钟之间。")
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "11025", "-f", "f32le", "-"], capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("无法解析音乐，请选择有效的 MP3 或 WAV 文件。") from exc
    samples = np.frombuffer(pcm, dtype="<f4")
    hop, win, sr = 256, 512, 11025
    if len(samples) < win:
        raise ValueError("音频数据太短。")
    # Spectral flux captures changes in content, rather than loudness alone.
    previous = np.zeros(win // 2 + 1)
    flux, energy = [], []
    window = np.hanning(win)
    for i in range(0, len(samples) - win, hop):
        chunk = samples[i:i+win] * window
        spectrum = np.abs(np.fft.rfft(chunk))
        flux.append(float(np.maximum(0, spectrum - previous).sum()))
        energy.append(float(np.sqrt(np.mean(chunk * chunk))))
        previous = spectrum
    flux = np.asarray(flux)
    flux = np.maximum(0, flux - np.convolve(flux, np.ones(17)/17, mode="same"))
    rate = sr / hop
    lo, hi = int(rate*60/190), min(len(flux)-1, int(rate*60/65))
    correlations = [float(np.dot(flux[:-lag], flux[lag:])) for lag in range(lo, hi+1)] if hi >= lo else []
    lag = lo + int(np.argmax(correlations)) if correlations else 0
    bpm = round(60 * rate / lag, 1) if lag and max(correlations, default=0) > 1e-8 else None
    threshold = float(np.percentile(flux, 70))
    peaks = [i for i in range(1, len(flux)-1) if flux[i] > max(threshold, 1e-6) and flux[i] >= flux[i-1] and flux[i] > flux[i+1]]
    # Pick measured onset peaks with a tempo-based refractory interval.
    selected = []
    min_gap = max(0.24, (60/bpm)*0.48) if bpm else .3
    for i in peaks:
        t = i / rate
        if not selected or t - selected[-1][0] >= min_gap:
            selected.append((t, float(flux[i])))
        elif flux[i] > selected[-1][1]:
            selected[-1] = (t, float(flux[i]))
    strength_threshold = float(np.percentile([s for _, s in selected], 75)) if selected else math.inf
    bands = []
    for start in range(0, math.ceil(duration), 8):
        block = energy[int(start*rate):int((start+8)*rate)]
        if block:
            bands.append({"start": start, "end": min(start+8, round(duration, 2)), "energy": round(float(np.mean(block)), 4)})
    return {"duration": round(duration, 2), "bpm": bpm, "beats": [round(t, 3) for t, _ in selected], "strong_beats": [round(t, 3) for t, s in selected if s >= strength_threshold], "segments": bands, "method": "spectral_flux_estimate"}
