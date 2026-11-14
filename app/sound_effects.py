"""Optional, locally synthesized transition cues. No network or bundled audio."""
import math
import wave
from pathlib import Path
import numpy as np

RATE = 44100


def reveal_time(scene, duration):
    spec=scene.get('spec') or {}; animation=spec.get('animation') or {}; params=spec.get('parameters') or {}
    delay=animation.get('narration_delay') or 0
    if animation.get('reveal_on_narration') and delay > 0:
        return min(float(delay), duration)
    start,end=animation.get('keyframes') or [0,.65]
    candidates=params.get('candidates') or []
    if scene.get('visual')=='question' and len(candidates)>1:
        target=(len(candidates)-1)/len(candidates)
        motion=animation.get('motion')
        raw=target if motion=='linear' else math.ceil(target*4)/4 if motion=='step' else 1-(1-target)**(1/3)
        return min(duration,max(0,(start+(end-start)*raw)*duration))
    return min(duration,max(0,end*duration))


def frame_reveal_time(scene, duration, beats, fps, engine):
    """Find the first frame where the current renderer shows its final state."""
    spec=scene.get('spec') or {}; animation=spec.get('animation') or {}; params=spec.get('parameters') or {}
    candidates=params.get('candidates') or []
    delay=float(animation.get('narration_delay') or 0)
    start,end=animation.get('keyframes') or [0,.65]
    for frame in range(max(1, math.ceil(duration*fps))):
        t=frame/fps
        if scene.get('visual')=='question' and len(candidates)>1 and animation.get('reveal_on_narration') and delay>0:
            if t>=delay: return t
            continue
        clock=t
        if engine=='browser' and animation.get('beat_binding',True) and len(beats)>1:
            passed=[b for b in beats if b<=t];last=passed[-1] if passed else 0
            if t-last<1.2: clock=last
        if engine=='light' and [start,end]==[0,.65]:
            progress=max(0,min(1,t/max(1,duration*.6)))
        else:
            raw=max(0,min(1,(clock/duration-start)/max(.01,end-start)))
            motion=animation.get('motion')
            progress=raw if motion=='linear' else math.floor(raw*4)/4 if motion=='step' else 1-(1-raw)**3
        if scene.get('visual')=='question' and candidates:
            if min(len(candidates)-1,math.floor(progress*len(candidates)))==len(candidates)-1: return t
        elif progress>=1:
            return t
    return max(0,(math.ceil(duration*fps)-1)/fps)


def write_track(scenes, durations, path, cue_times=None):
    if not any((s.get('spec',{}).get('animation') or {}).get('sound_effect')=='soft_reveal' for s in scenes):
        return None
    path=Path(path)
    with wave.open(str(path),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(RATE)
        for index,(scene,duration) in enumerate(zip(scenes,durations)):
            frames=max(1,round(duration*RATE)); data=np.zeros(frames,dtype=np.float32)
            if (scene.get('spec',{}).get('animation') or {}).get('sound_effect')=='soft_reveal':
                cue = cue_times[index] if cue_times is not None else reveal_time(scene,duration)
                start=min(frames,round(cue*RATE)); count=min(round(.24*RATE),frames-start)
                if count>0:
                    t=np.arange(count,dtype=np.float32)/RATE
                    envelope=np.minimum(1,t/.012)*np.exp(-t*16)
                    data[start:start+count]=.065*envelope*(np.sin(2*np.pi*660*t)+.35*np.sin(2*np.pi*990*t))/1.35
            output.writeframes(np.asarray(np.clip(data,-1,1)*32767,dtype='<i2').tobytes())
    return path
