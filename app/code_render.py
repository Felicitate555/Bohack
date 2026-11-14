"""Scene-specific generated animation; untrusted code only runs in containers."""
import json
import hashlib
import inspect
import math
import subprocess
import uuid
from pathlib import Path

from . import browser_render, render, style_profile
from .services import code_video, speech
from .agents import visual_reviewer
from .providers import model_registry


def scene_cache_key(topic, scene, profile, timeline, sequence, fps, width, height):
    sandbox = Path(__file__).resolve().parent.parent / 'sandbox'
    dependencies = {name: hashlib.sha256((sandbox / name).read_bytes()).hexdigest() for name in ('worker.py', 'Dockerfile')}
    payload = {'version':1, 'topic':topic, 'scene':scene, 'profile':profile, 'timeline':timeline,
               'sequence':sequence, 'fps':fps, 'width':width, 'height':height,
               'model':model_registry.model('animation'), 'reviewer':model_registry.model('reviewer'),
               'review_prompt':hashlib.sha256(inspect.getsource(visual_reviewer.review).encode('utf-8')).hexdigest(),
               'prompt':code_video.SYSTEM, 'sandbox':dependencies}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def restore_cached_scene(cache, outdir, index):
    try:
        meta = json.loads((cache / 'manifest.json').read_text(encoding='utf-8'))
        files = {'clip':cache / 'scene.mp4', 'source':cache / 'scene.py', 'review':cache / 'review.json'}
        contents = {key:path.read_bytes() for key,path in files.items()}
        if any(hashlib.sha256(value).hexdigest() != meta[key] for key,value in contents.items()):
            return None
        review = json.loads(contents['review'])
        if not isinstance(review, dict) or review.get('status') != 'reviewed':
            return None
        for key, name in (('clip', f'scene_{index}.mp4'), ('source', f'scene_{index}.py'), ('review', f'scene_{index}_review.json')):
            (outdir / name).write_bytes(contents[key])
        return review
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_cached_scene(cache, outdir, index):
    cache.mkdir(parents=True, exist_ok=True)
    files = {'clip':outdir / f'scene_{index}.mp4', 'source':outdir / f'scene_{index}.py', 'review':outdir / f'scene_{index}_review.json'}
    contents = {key:path.read_bytes() for key,path in files.items()}
    token = uuid.uuid4().hex
    for key, name in (('clip','scene.mp4'),('source','scene.py'),('review','review.json')):
        tmp = cache / (name + '.' + token)
        tmp.write_bytes(contents[key]); tmp.replace(cache / name)
    tmp = cache / ('manifest.json.' + token)
    tmp.write_text(json.dumps({key:hashlib.sha256(value).hexdigest() for key,value in contents.items()}),encoding='utf-8')
    tmp.replace(cache / 'manifest.json')


def review_clip(scene, clip, duration, profile):
    frames = []
    for i, fraction in enumerate((.2, .5, .8)):
        frame = clip.with_name(f'{clip.stem}_review_{i}.jpg')
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(duration*fraction), '-i', str(clip),
                        '-frames:v', '1', '-vf', 'scale=640:-2', str(frame)], check=True, timeout=30, capture_output=True)
        frames.append(frame)
    return visual_reviewer.review(scene, frames, profile=profile)


def render_video(project, outdir, voice, update, quality='preview', music=None,
                 beat_timeline=None, music_volume=.18, voice_provider='edge',
                 voice_name='anna', voice_speed=1.0, report=lambda event: None,
                 narration_mode='scene'):
    check = getattr(update, 'check_cancel', lambda: None)
    code_video.check_available()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    scenes = project['scenes']
    fps = 30 if quality == 'full' else 15
    width, height = (1920, 1080) if quality == 'full' else (960, 540)
    profile = style_profile.normalize(project.get('style_profile'))
    instruction = speech.narration_instruction(voice_provider, profile) if voice else None
    voice_durations, cues = [], []
    if voice:
        if narration_mode == 'sentence':
            speech.create_segmented(scenes, outdir, voice_provider, voice_name, voice_speed,
                                    update, report, cache_dir=outdir.parent / 'speech_cache', instruction=instruction)
        else:
            speech.create(scenes, outdir, voice_provider, voice_name, voice_speed, update, report,
                          instruction=instruction)
        for i, scene in enumerate(scenes):
            duration = speech.decoded_duration(outdir / f'voice_{i}.mp3')
            voice_durations.append(duration)
            cues.append(speech.read_cues(outdir / f'voice_{i}.cues.json', scene['narration'], duration)
                        if narration_mode == 'sentence' else None)
    delays = [render.scene_narration_delay(s) for s in scenes]
    minimums = [max(3.0, voice_durations[i] + .6 + delays[i]) if voice else
                max(4.0, min(12.0, len(s['narration']) / 4.2 + .8) + delays[i])
                for i, s in enumerate(scenes)]
    extension = max(0, (project.get('target_seconds') or 0) - sum(minimums)) / len(scenes)
    beats, _ = browser_render.usable_beats(beat_timeline)
    elapsed, durations, clips, srt = 0.0, [], [], []
    for i, scene in enumerate(scenes):
        check()
        duration = minimums[i] + extension
        boundary = elapsed + duration
        nearby = [b for b in beats if boundary <= b <= boundary + .65]
        if nearby: duration = nearby[0] - elapsed
        duration = math.ceil(duration * fps) / fps
        speech_duration = voice_durations[i] if voice else duration - delays[i]
        local_cues = list(render.caption_timeline(scene['narration'], duration, speech_duration,
                                                  cues[i] if voice else None, delays[i]))
        captions = [{'text': text, 'start': start, 'end': end} for text, start, end in local_cues]
        timing = {'duration': duration, 'caption_cues': captions,
                  'estimated_beats': [round(b - elapsed, 3) for b in beats if elapsed <= b < elapsed + duration][:120],
                  'scene_index': i, 'scene_count': len(scenes)}
        sequence = [{'heading': s['heading'], 'narration': s['narration'], 'visual_brief': s.get('visual_brief', '')}
                    for s in scenes]
        for text, start, end in local_cues:
            srt.append(f'{len(srt)+1}\n{render.stamp(elapsed+start)} --> {render.stamp(elapsed+end)}\n{text}\n')
        cache_key = scene_cache_key(project.get('topic', ''), scene, profile, timing, sequence, fps, width, height)
        cache = outdir.parent / 'code_cache' / cache_key
        cached_review = restore_cached_scene(cache, outdir, i)
        if cached_review is not None:
            update(f'复用已渲染的镜头 {i+1}/{len(scenes)}')
            if cached_review.get('repair') == 'rendered_not_re_reviewed':
                project.setdefault('warnings', []).append(f'镜头 {i+1} 已按审片意见修复，但修复画面尚未再次审查。')
            clips.append(outdir / f'scene_{i}.mp4')
            durations.append(duration)
            elapsed += duration
            continue
        update(f'正在编写镜头 {i+1}/{len(scenes)} 的专属动画代码')
        code, event = code_video.generate_code(project.get('topic', ''), scene, profile=profile, timeline=timing, sequence=sequence)
        report(event)
        last_error = None
        for attempt in range(3):
            check()
            if attempt:
                update(f'正在修复镜头 {i+1} 的动画代码（第 {attempt} 次）')
                code, event = code_video.generate_code(project.get('topic', ''), scene, previous=code,
                                                       error=str(last_error), profile=profile, timeline=timing, sequence=sequence)
                report(event)
            try:
                update(f'正在隔离渲染镜头 {i+1}/{len(scenes)}')
                video = code_video.render_sandbox({'code': code, 'width': width, 'height': height,
                                                   'fps': fps, 'duration': duration, 'captions': captions},
                                                  check_cancel=check)
                break
            except RuntimeError as exc:
                last_error = exc
        else:
            raise RuntimeError(f'镜头 {i+1} 的代码经过修复仍无法渲染：{last_error}')
        clip = outdir / f'scene_{i}.mp4'
        clip.write_bytes(video)
        review_record = {'status': 'not_reviewed'}
        try:
            check()
            update(f'正在审查镜头 {i+1} 的实际画面')
            review, event = review_clip(scene, clip, duration, profile)
            report(event)
            if not isinstance(review, dict):
                raise ValueError('审片结果格式无效')
            review_record = {'status': 'reviewed', 'result': review, 'repair': 'not_needed'}
            issues = review.get('issues') if isinstance(review.get('issues'), list) else []
            high = [issue for issue in issues if isinstance(issue, dict) and issue.get('severity') == 'high']
            if review.get('needs_revision') is True and high:
                check()
                update(f'正在按审片意见修复镜头 {i+1}')
                feedback = json.dumps(high[:3], ensure_ascii=False)[:1500]
                try:
                    revised, event = code_video.generate_code(project.get('topic', ''), scene, previous=code,
                        error='实际画面审查发现的问题：'+feedback, profile=profile, timeline=timing, sequence=sequence)
                    report(event)
                    fixed = code_video.render_sandbox({'code': revised, 'width': width, 'height': height,
                        'fps': fps, 'duration': duration, 'captions': captions}, check_cancel=check)
                    candidate = clip.with_name(clip.stem + '_repaired.mp4')
                    candidate.write_bytes(fixed)
                    candidate.replace(clip)
                    code = revised
                    review_record['repair'] = 'rendered_not_re_reviewed'
                    project.setdefault('warnings', []).append(f'镜头 {i+1} 已按审片意见修复，但修复画面尚未再次审查。')
                except render.RenderCancelled:
                    raise
                except Exception as exc:
                    review_record['repair'] = 'failed_original_preserved'
                    project.setdefault('warnings', []).append(f'镜头 {i+1} 审片后修复失败，保留原画面：{str(exc)[:200]}')
        except render.RenderCancelled:
            raise
        except Exception as exc:
            review_record = {'status': 'failed', 'message': str(exc)[:300]}
            project.setdefault('warnings', []).append(f'镜头 {i+1} 自动审片未完成：{str(exc)[:200]}')
        (outdir / f'scene_{i}_review.json').write_text(json.dumps(review_record, ensure_ascii=False), encoding='utf-8')
        (outdir / f'scene_{i}.py').write_text(code, encoding='utf-8')
        if review_record.get('status') == 'reviewed' and review_record.get('repair') != 'failed_original_preserved':
            try:
                save_cached_scene(cache, outdir, i)
            except OSError:
                project.setdefault('warnings', []).append(f'镜头 {i+1} 缓存保存失败，后续重新导出可能需要重做。')
        clips.append(clip)
        durations.append(duration)
        elapsed += duration
    update('正在合成画面、配音与音乐')
    (outdir / 'concat.txt').write_text(''.join("file '" + p.name + "'\n" for p in clips), encoding='utf-8')
    base, output = outdir / 'combined.mp4', outdir / 'video.mp4'
    render.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i',
                str(outdir / 'concat.txt'), '-c', 'copy', str(base)])
    if voice or music:
        browser_render.mux_audio(base, output, [outdir / f'voice_{i}.mp3' for i in range(len(scenes))]
                                if voice else [], durations, music, music_volume, delays)
    else:
        base.replace(output)
    check()
    (outdir / 'subtitles.srt').write_text('\n'.join(srt), encoding='utf-8')
    return round(elapsed, 2)
