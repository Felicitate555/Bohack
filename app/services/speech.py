import asyncio
import hashlib
import json
import math
import re
import shutil
import subprocess
import uuid
from ..providers import siliconflow
from ..providers.model_registry import model, base_url


def sentence_chunks(text, width=27):
    """Split at sentence boundaries, then at nearby punctuation for captions."""
    chunks = []
    for sentence in re.split(r'(?<=[。！？!?；;])', str(text)):
        sentence = sentence.strip()
        while len(sentence) > width:
            part = sentence[:width]
            cut = max((part.rfind(c) for c in '，、,；;'), default=-1)
            length = cut + 1 if cut >= width // 2 else width
            chunks.append(sentence[:length].strip())
            sentence = sentence[length:].strip()
        if sentence: chunks.append(sentence)
    return [part for part in chunks if part]


def voice_model_id(provider):
    if provider == 'edge': return 'edge-tts'
    endpoint = hashlib.sha256(base_url().encode('utf-8')).hexdigest()[:12]
    return model('speech_alt' if provider == 'moss' else 'speech') + '@' + endpoint


def narration_instruction(provider, profile):
    if provider == 'siliconflow' and model('speech') == 'FunAudioLLM/CosyVoice2-0.5B':
        return profile.get('narration_instruction')
    return None


def voice_cache_key(narration, provider, voice, speed, mode, instruction=None):
    return [2, narration, provider, voice, speed, mode, voice_model_id(provider), instruction or '']


def decoded_duration(path):
    try:
        probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)], capture_output=True, text=True, check=True, timeout=20)
        duration = float(probe.stdout.strip())
        if not 0 < duration <= 60:
            raise ValueError('单句音频时长无效')
        pcm = subprocess.run(['ffmpeg','-v','error','-i',str(path),'-ac','1','-ar','16000','-f','s16le','-'], capture_output=True, check=True, timeout=40).stdout
        if not pcm: raise ValueError('单句音频为空')
        return len(pcm) / 32000
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError('无法读取单句配音时长，请检查音频和 FFmpeg。') from exc


def read_cues(path, narration, duration=None):
    try:
        cues = json.loads(path.read_text(encoding='utf-8'))
        chunks = sentence_chunks(narration)
        if not isinstance(cues, list) or not cues or len(cues) != len(chunks) or len(cues) > 32:
            raise ValueError()
        previous = 0.0
        for cue, text in zip(cues, chunks):
            if not isinstance(cue, dict) or cue.get('text') != text: raise ValueError()
            start, end = cue.get('start'), cue.get('end')
            if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in (start, end)):
                raise ValueError()
            if start < previous or end <= start or (duration is not None and end > duration + .1): raise ValueError()
            previous = end
        if cues[0]['start'] != 0: raise ValueError()
        return cues
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('逐句配音时间记录无效，请重新生成配音。') from exc


def create_segmented(scenes, outdir, provider, voice, speed, update, report, indices=None, cache_dir=None, instruction=None):
    """Synthesize short phrases and record decoded durations for captions."""
    all_chunks = [sentence_chunks(s['narration']) for s in scenes]
    if sum(map(len, all_chunks)) > 32:
        raise ValueError('逐句配音最多 32 段，请缩短口播或选择整镜配音。')
    if cache_dir is not None: cache_dir.mkdir(parents=True, exist_ok=True)
    indices = range(len(scenes)) if indices is None else indices
    for i in indices:
        chunks = all_chunks[i]
        if not chunks: raise ValueError('镜头旁白为空。')
        paths, cues, elapsed = [], [], 0.0
        for j, text in enumerate(chunks):
            update(f'正在准备镜头 {i+1}/{len(scenes)} 的第 {j+1}/{len(chunks)} 句配音')
            folder = outdir / 'speech_segments' / f'{i}_{j}'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / 'voice_0.mp3'
            identity = json.dumps(voice_cache_key(text, provider, voice, speed, 'sentence', instruction), ensure_ascii=False, separators=(',', ':'))
            cached = cache_dir / (hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.mp3') if cache_dir is not None else None
            length = None
            if cached and cached.is_file():
                try:
                    length = decoded_duration(cached)
                    shutil.copyfile(cached, path)
                    update(f'复用镜头 {i+1}/{len(scenes)} 的第 {j+1}/{len(chunks)} 句配音')
                except (OSError, ValueError):
                    length = None
            if length is None:
                update(f'正在生成镜头 {i+1}/{len(scenes)} 的第 {j+1}/{len(chunks)} 句配音')
                if instruction:
                    create([{'narration':text}], folder, provider, voice, speed, lambda _: None, report, instruction=instruction)
                else:
                    create([{'narration':text}], folder, provider, voice, speed, lambda _: None, report)
                length = decoded_duration(path)
                if cached:
                    temp = cached.with_name(cached.name + '.' + uuid.uuid4().hex + '.tmp')
                    try:
                        shutil.copyfile(path, temp)
                        temp.replace(cached)
                    finally:
                        temp.unlink(missing_ok=True)
            cues.append({'text':text, 'start':round(elapsed, 4), 'end':round(elapsed + length, 4)})
            elapsed += length + (.15 if j < len(chunks)-1 else 0)
            paths.append(path)
        cmd = ['ffmpeg','-y','-v','error']
        for path in paths: cmd += ['-i',str(path)]
        filters = []
        for j, cue in enumerate(cues):
            length = cue['end'] - cue['start'] + (.15 if j < len(cues)-1 else 0)
            filters.append(f'[{j}:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,apad,atrim=duration={length},asetpts=PTS-STARTPTS[s{j}]')
        filters.append(''.join(f'[s{j}]' for j in range(len(cues))) + f'concat=n={len(cues)}:v=0:a=1[a]')
        cmd += ['-filter_complex',';'.join(filters),'-map','[a]','-c:a','libmp3lame','-b:a','128k',str(outdir/f'voice_{i}.mp3')]
        try: subprocess.run(cmd, check=True, capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError) as exc: raise ValueError('逐句配音合成失败，请检查 FFmpeg。') from exc
        (outdir/f'voice_{i}.cues.json').write_text(json.dumps(cues,ensure_ascii=False),encoding='utf-8')


def create(scenes, outdir, provider, voice, speed, update, report, indices=None, instruction=None):
    indices = list(range(len(scenes))) if indices is None else list(indices)
    if provider == "edge":
        voices = {"anna": "zh-CN-XiaoxiaoNeural", "claire": "zh-CN-XiaoyiNeural", "diana": "zh-CN-XiaoxiaoNeural", "alex": "zh-CN-YunxiNeural", "benjamin": "zh-CN-YunyangNeural"}
        if voice not in voices:
            raise siliconflow.ProviderError("Edge TTS 不支持所选音色，请选择可用音色。")
        import edge_tts
        async def run():
            for i in indices:
                scene = scenes[i]
                update(f"正在生成配音 {i+1}/{len(scenes)}")
                edge_voice = voices[voice]
                rate = f"{(float(speed) - 1) * 100:+.0f}%"
                await edge_tts.Communicate(scene["narration"], edge_voice, rate=rate).save(str(outdir / f"voice_{i}.mp3"))
        asyncio.run(run())
        return
    role = "speech_alt" if provider == "moss" else "speech"
    for i in indices:
        scene = scenes[i]
        update(f"正在生成配音 {i+1}/{len(scenes)}")
        try:
            data = siliconflow.speech(scene["narration"], voice, speed, role, instruction=instruction)
        except siliconflow.ProviderError as exc:
            raise siliconflow.ProviderError(f"第 {i+1} 个镜头配音失败：{exc}") from exc
        (outdir / f"voice_{i}.mp3").write_bytes(data)
        ident = model(role)
        sent_text = siliconflow.speech_input(scene["narration"], ident, instruction)
        report({"role": "speech", "model": ident, "utf8_bytes": len(sent_text.encode("utf-8")), "status": "complete"})
