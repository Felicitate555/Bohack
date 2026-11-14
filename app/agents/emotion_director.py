"""Bounded emotional direction compiled into supported scene timing, never code."""


def _text(value, limit=160):
    return value.strip()[:limit] if isinstance(value, str) else ''


def build(raw, scenes):
    if not isinstance(raw, dict) or not _text(raw.get('emotional_goal')) or not isinstance(raw.get('beats'), list) or not raw['beats']:
        return {'status':'fallback', 'message':'情绪规划未返回有效节点，保留基础分镜；未应用额外情绪动作。', 'beats':[]}
    result = {key:_text(raw.get(key), 240) for key in ('emotional_goal','audience_expectation','core_tension','payoff','arc')}
    result.update(status='generated', message='情绪计划由模型生成；应用状态仅说明引擎参数已设置，效果需审片。', beats=[])
    occupied = set()
    for item in raw['beats'][:16]:
        if not isinstance(item, dict): continue
        index=item.get('scene_index')
        if isinstance(index,bool) or not isinstance(index,int) or not 0 <= index < len(scenes): continue
        execution=_text(item.get('execution'),60)
        beat={'scene_index':index,'kind':_text(item.get('kind'),40),'knowledge_goal':_text(item.get('knowledge_goal')),
              'execution':execution,'applied':False,'actual':'未应用：当前引擎不支持该动作或镜头条件不满足。'}
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
        elif execution == 'progressive' and scene['visual'] in {'dots','network','distinct','flow','timeline','growth','bars','curve','scattering'}:
            scene['spec']['animation']['keyframes']=[.08,.8]
            scene['spec']['animation']['motion']='smooth'
            beat.update(applied=True,actual='浏览器引擎通过现有组件逐步建立画面，镜头 80% 左右完成；轻量引擎支持范围较小。')
        if beat['applied']: occupied.add(index)
        result['beats'].append(beat)
    if not result['beats']:
        return {'status':'fallback','message':'情绪节点无效，保留基础分镜；未应用额外情绪动作。','beats':[]}
    return result
