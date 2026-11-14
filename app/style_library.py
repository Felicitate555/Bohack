"""Local reference measurements and a declarative, private style library."""
import json
import re
import subprocess
import uuid
from pathlib import Path

import numpy as np
from . import audio, style_profile


def summarize_frames(frames):
    pixels = frames.reshape(-1, 3).astype(np.float32)
    luminance = pixels @ np.array([.2126, .7152, .0722])
    quantized = (pixels.astype(np.uint8) // 32).astype(np.int32)
    codes = quantized[:, 0] * 64 + quantized[:, 1] * 8 + quantized[:, 2]
    counts = np.bincount(codes, minlength=512)
    colors = []
    for code in np.argsort(counts)[::-1][:8]:
        if counts[code] == 0: continue
        colors.append('#%02x%02x%02x' % (((code//64)*32+16), (((code//8)%8)*32+16), ((code%8)*32+16)))
    motion = float(np.abs(np.diff(frames.astype(np.float32), axis=0)).mean()/255) if len(frames)>1 else 0
    bottom = frames[:, 67:85].astype(np.float32)
    gray = frames.astype(np.float32).mean(axis=(0, 3))
    edge = np.zeros_like(gray)
    edge[:, 1:] += np.abs(np.diff(gray, axis=1))
    edge[1:, :] += np.abs(np.diff(gray, axis=0))
    total = float(edge.sum())
    center = [round(float((edge * np.arange(160)[None, :]).sum() / total / 159), 3),
              round(float((edge * np.arange(90)[:, None]).sum() / total / 89), 3)] if total else [.5, .5]
    return {'frame_count':len(frames), 'colors':colors, 'mean_luminance':round(float(luminance.mean()/255),3),
            'motion_intensity':round(motion,3), 'edge_center':center, 'lower_region_contrast':round(float(bottom.std()/255),3),
            'limitations':'本地稀疏抽帧仅估计颜色、亮度、画面变化、边缘分布和下方区域对比度；不能识别字幕、语义、真实镜头运动或完整风格。'}


def contrast_ratio(first, second):
    def luminance(color):
        channels = [int(color[i:i+2],16)/255 for i in (1,3,5)]
        linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return .2126*linear[0]+.7152*linear[1]+.0722*linear[2]
    a,b=sorted((luminance(first),luminance(second)))
    return (b+.05)/(a+.05)


def readable_accent(color, background, threshold):
    rgb=[int(color[i:i+2],16) for i in (1,3,5)]
    direction=0 if contrast_ratio('#000000',background)>contrast_ratio('#ffffff',background) else 255
    for step in range(101):
        adjusted=[round(v+(direction-v)*step/100) for v in rgb]
        candidate='#%02x%02x%02x' % tuple(adjusted)
        if contrast_ratio(candidate,background)>=threshold:return candidate
    return '#000000' if direction==0 else '#ffffff'


def profile_from_analysis(result):
    colors = result.get('colors') or []
    profile = style_profile.normalize()
    if colors:
        profile['palette']['background'] = min(colors, key=lambda c: sum(int(c[i:i+2],16) for i in (1,3,5)))
        candidates = [c for c in colors if sum(int(c[i:i+2],16) for i in (1,3,5)) >= 180]
        if candidates:
            chosen = max(candidates, key=lambda c: max(int(c[i:i+2],16) for i in (1,3,5))-min(int(c[i:i+2],16) for i in (1,3,5)))
            rgb = [int(chosen[i:i+2],16) for i in (1,3,5)]
            factor = max(1, 180 / max(rgb))
            profile['palette']['accent'] = '#%02x%02x%02x' % tuple(min(255, round(v*factor)) for v in rgb)
        profile['palette']['surface'] = profile['palette']['background']
        profile['palette']['glow'] = profile['palette']['background']
        background = profile['palette']['background']
        profile['palette']['text'] = readable_accent(profile['palette']['text'], background, 4.5)
        profile['palette']['muted'] = readable_accent(profile['palette']['muted'], background, 4.5)
        profile['palette']['accent'] = readable_accent(profile['palette']['accent'], background, 4.5)
        profile['palette']['secondary'] = readable_accent(profile['palette']['secondary'], background, 4.5)
    profile['name'] = '参考视频风格'
    profile['direction'] = '依据本地参考视频的基础色彩调整，构图和具体叙事请按内容设计。'
    profile['media_prompt'] = 'cinematic documentary aesthetic, palette ' + ', '.join(colors[:4]) + ', coherent lighting, clear space for graphics, no text'
    return profile


def analyze_reference(path):
    try:
        probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,check=True,timeout=20)
        info=json.loads(probe.stdout); duration=float(info['format']['duration'])
        if not 1 <= duration <= 600: raise ValueError('参考视频须为 1 秒至 10 分钟。')
        if not any(s.get('codec_type')=='video' for s in info['streams']): raise ValueError('文件中没有视频画面。')
        cmd=['ffmpeg','-v','error','-i',str(path),'-vf',f'fps={min(1, 8/duration)},scale=160:90', '-frames:v','8','-f','rawvideo','-pix_fmt','rgb24','-']
        raw=subprocess.run(cmd,capture_output=True,check=True,timeout=90).stdout
        frame_size=160*90*3
        if len(raw)<frame_size or len(raw)%frame_size: raise ValueError('无法读取参考视频画面。')
        result=summarize_frames(np.frombuffer(raw,dtype=np.uint8).reshape(-1,90,160,3))
        result['duration']=round(duration,2)
        result['audio_rhythm']={'status':'无音轨或超出节拍分析时长'}
        if duration<=300 and any(s.get('codec_type')=='audio' for s in info['streams']):
            try:
                rhythm=audio.analyze(path)
                result['audio_rhythm']={'status':rhythm['rhythm_status'], 'bpm':rhythm['bpm'], 'periodicity':rhythm['periodicity']}
            except ValueError:
                result['audio_rhythm']={'status':'音轨节拍无法可靠分析'}
        return {'analysis':result,'profile':profile_from_analysis(result)}
    except (OSError,subprocess.SubprocessError,KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('无法解析参考视频，请选择有效 MP4。') from exc


def list_profiles(root):
    items=[style_profile.normalize()]
    for path in (root/'styles').glob('*.json'):
        try: items.append(style_profile.normalize(json.loads(path.read_text(encoding='utf-8'))))
        except (OSError,ValueError): continue
    return items


def get(root, identifier):
    if identifier == style_profile.DEFAULT['id']: return style_profile.normalize()
    if not re.fullmatch(r'[0-9a-f]{32}', identifier): raise ValueError('风格不存在。')
    path=root/'styles'/(identifier+'.json')
    try: return style_profile.normalize(json.loads(path.read_text(encoding='utf-8')))
    except (OSError,ValueError) as exc: raise ValueError('风格不存在。') from exc


def save(root, raw):
    if not isinstance(raw,dict) or not isinstance(raw.get('name'),str) or not raw['name'].strip(): raise ValueError('请填写风格名称。')
    profile=style_profile.normalize(raw)
    identifier=raw.get('id','')
    if not re.fullmatch(r'[0-9a-f]{32}',identifier): identifier=uuid.uuid4().hex
    profile['id']=identifier
    folder=root/'styles'; folder.mkdir(parents=True,exist_ok=True)
    tmp=folder/(identifier+'.tmp'); tmp.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(folder/(identifier+'.json'))
    return profile
