"""Bounded emotional direction compiled into supported scene timing, never code."""
from ..scene_spec import bounded


def _text(value, limit=160):
    return value.strip()[:limit] if isinstance(value, str) else ''


def build(raw, scenes):
    if not isinstance(raw, dict) or not _text(raw.get('emotional_goal')) or not isinstance(raw.get('beats'), list) or not raw['beats']:
        return {'status':'fallback', 'message':'情绪规划未返回有效节点，保留基础分镜；未应用额外情绪动作。', 'beats':[]}
    result = {key:_text(raw.get(key), 240) for key in ('emotional_goal','audience_expectation','core_tension','payoff','arc')}
    result.update(status='generated', message='情绪计划由模型生成；应用状态仅说明引擎参数已设置，效果需审片。', beats=[])
    occupied = set()
    if len(raw['beats']) > 16:
        result['message'] += ' 超过 16 个节点的部分未应用。'
    for item in raw['beats'][:16]:
        if not isinstance(item, dict):
            result['beats'].append({'scene_index':None,'kind':'','knowledge_goal':'','execution':'','applied':False,'actual':'未应用：情绪节点格式无效。'})
            continue
        index=item.get('scene_index')
        execution=_text(item.get('execution'),60)
        valid_index=not isinstance(index,bool) and isinstance(index,int) and 0 <= index < len(scenes)
        beat={'scene_index':index if valid_index else None,'kind':_text(item.get('kind'),40),'knowledge_goal':_text(item.get('knowledge_goal')),
              'execution':execution,'applied':False,'actual':'未应用：当前引擎不支持该动作或镜头条件不满足。'}
        if not valid_index:
            beat['actual']='未应用：情绪节点的镜头编号无效。'
            result['beats'].append(beat)
            continue
        scene=scenes[index]
        if index in occupied:
            beat['actual']='未应用：此镜头已有情绪节奏动作，避免覆盖。'
            result['beats'].append(beat)
            continue
        if execution == 'delayed_reveal' and scene['visual']=='question' and len(scene['spec']['parameters']['candidates'])>=2:
            animation=scene['spec']['animation']
            animation['keyframes']=[.12,.85]
            animation['motion']='linear'
            beat.update(applied=True,actual='候选项从镜头 12% 开始变化，85% 完成；须人工检查标题、旁白和字幕是否提前揭晓。')
        elif execution == 'progressive' and scene['visual'] in {'dots','network','distinct','flow','timeline','growth','bars','curve','scattering','binary_search'}:
            scene['spec']['animation']['keyframes']=[.08,.8]
            scene['spec']['animation']['motion']='smooth'
            beat.update(applied=True,actual='浏览器引擎通过现有组件逐步建立画面，镜头 80% 左右完成；轻量引擎支持范围较小。')
        if beat['applied']:
            pause = bounded(item.get('pause_before_narration'), 0, 2)
            if pause is not None:
                scene['spec']['animation']['narration_delay'] = pause
                beat['narration_delay'] = pause
                if execution == 'delayed_reveal' and pause > 0:
                    scene['spec']['animation']['reveal_on_narration'] = True
                    beat['actual'] = f'答案与旁白、字幕在 {pause:g} 秒后同时出现；请核对标题是否提前泄露。'
                else:
                    beat['actual'] += f' 旁白和字幕延后 {pause:g} 秒。'
            if execution == 'delayed_reveal' and item.get('sound_effect') == 'soft_reveal':
                scene['spec']['animation']['sound_effect'] = 'soft_reveal'
                beat['sound_effect'] = 'soft_reveal'
                beat['actual'] += ' 已启用本地合成的轻微揭晓音效。'
            elif item.get('sound_effect') not in (None, '', 'none'):
                beat['actual'] += ' 请求的音效不受支持，未应用。'
            if execution == 'delayed_reveal':
                answer = scene['spec']['parameters']['candidates'][-1].strip()
                if len(answer) >= 2:
                    if answer in scene.get('heading', ''):
                        beat['actual'] += ' 注意：标题已包含最终候选，可能提前揭晓。'
                    if answer in scene.get('detail', ''):
                        beat['actual'] += ' 注意：补充文字已包含最终候选，可能提前揭晓。'
                    if not scene['spec']['animation'].get('narration_delay') and answer in scene.get('narration', ''):
                        beat['actual'] += ' 注意：无旁白前停顿且口播包含最终候选，请检查揭晓时序。'
            occupied.add(index)
        result['beats'].append(beat)
    if not any(beat['scene_index'] is not None for beat in result['beats']):
        result['status']='fallback'
        result['message']='情绪节点无有效镜头，保留基础分镜；未应用额外情绪动作。'
    return result
