import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import director, main


class EmotionalDirectionTests(unittest.TestCase):
    def scene(self):
        return {'heading':'多少人会撞生日？','narration':'你猜需要多少人？','key':'猜一猜','visual':'question',
                'spec':{'parameters':{'candidates':['100','50','23']}}}

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY':'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_fast_emotion_plan_changes_renderable_reveal_without_extra_call(self, chat):
        chat.return_value=(json.dumps({'title':'生日','scenes':[self.scene()], 'emotion_plan':{
            'emotional_goal':'好奇之后理解','core_tension':'人数与直觉差异','payoff':'解释配对',
            'beats':[{'scene_index':0,'kind':'anticipation','execution':'delayed_reveal','knowledge_goal':'猜测人数'}]}},ensure_ascii=False),{'role':'writer'})
        plan, usage=director.generate('生日',mode='fast')
        self.assertEqual(chat.call_count,1)
        self.assertEqual(plan['emotion_plan']['status'],'generated')
        self.assertEqual(plan['emotion_plan']['beats'][0]['applied'],True)
        self.assertEqual(plan['scenes'][0]['spec']['animation']['keyframes'],[.12,.85])
        self.assertEqual(plan['scenes'][0]['spec']['animation']['motion'],'linear')
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['candidates'],['100','50','23'])

    def test_narration_delay_shifts_subtitles_and_preserves_bounded_scene_setting(self):
        from app import render
        scene=self.scene(); scene['spec']['animation']={'narration_delay':1.2}
        saved=director.validate({'scenes':[scene]})['scenes'][0]
        self.assertEqual(saved['spec']['animation']['narration_delay'],1.2)
        self.assertEqual(director.validate({'scenes':[saved]})['scenes'][0]['spec']['animation']['narration_delay'],1.2)
        cues=[{'text':'你猜需要多少人？','start':0,'end':.8}]
        self.assertEqual(list(render.caption_timeline(saved['narration'],3,1,cues,delay=1.2)), [('你猜需要多少人？',1.2,3)])
        saved['spec']['timing']={'speech_duration':1,'narration_delay':1.2,'caption_cues':cues}
        with patch.object(render, 'text_fit') as text:
            render.frame(saved,0,1,.5,3)
            self.assertNotIn(saved['narration'],[c.args[2] for c in text.call_args_list])
        scene['spec']['animation']['narration_delay']=99
        self.assertEqual(director.validate({'scenes':[scene]})['scenes'][0]['spec']['animation']['narration_delay'],0)

    def test_unvoiced_caption_estimates_fit_after_pause(self):
        from app import render
        parts=list(render.caption_timeline('第一句很长很长很长很长，第二句也很长很长很长很长，最后结尾。',4,4,delay=2))
        self.assertTrue(parts)
        self.assertTrue(all(2 <= start < end <= 4 for _,start,end in parts))

    def test_muxed_narration_has_real_leading_silence(self):
        import subprocess
        import numpy as np
        from app import browser_render
        with tempfile.TemporaryDirectory(dir='data') as d:
            root=Path(d); base=root/'base.mp4'; tone=root/'tone.wav'; result=root/'out.mp4'
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=size=160x90:rate=15:duration=3',
                            '-c:v','libx264','-pix_fmt','yuv420p',str(base)],check=True)
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=1',str(tone)],check=True)
            browser_render.mux_audio(base,result,[tone],[3],None,.18,[1.2])
            pcm=subprocess.run(['ffmpeg','-v','error','-i',str(result),'-ac','1','-ar','16000','-f','f32le','-'],check=True,capture_output=True).stdout
            samples=np.frombuffer(pcm,dtype='<f4')
            rms=lambda a,b: float(np.sqrt(np.mean(samples[int(a*16000):int(b*16000)]**2)))
            self.assertLess(rms(.3,1.0),.001)
            self.assertGreater(rms(1.4,2.0),.02)
            self.assertLess(rms(2.5,2.8),.001)

    def test_emotion_director_respects_requested_pause(self):
        from app.agents import emotion_director
        p=director.validate({'scenes':[self.scene()]})
        result=emotion_director.build({'emotional_goal':'等待','beats':[{'scene_index':0,'kind':'期待',
            'execution':'delayed_reveal','pause_before_narration':1.2}]},p['scenes'])
        self.assertEqual(p['scenes'][0]['spec']['animation']['narration_delay'],1.2)
        self.assertEqual(result['beats'][0]['narration_delay'],1.2)
        self.assertTrue(p['scenes'][0]['spec']['animation']['reveal_on_narration'])
        from app import render
        p['scenes'][0]['spec']['timing']={'speech_duration':1,'narration_delay':1.2}
        with patch.object(render,'text_fit') as text:
            render.frame(p['scenes'][0],0,1,1,5)
            self.assertNotIn('23',[c.args[2] for c in text.call_args_list])
        with patch.object(render,'text_fit') as text:
            render.frame(p['scenes'][0],0,1,1.2,5)
            self.assertIn('23',[c.args[2] for c in text.call_args_list])

    def test_scattering_uses_gradual_discovery_instead_of_guess_sequence(self):
        from app.agents import emotion_director
        scene={'heading':'同一束阳光，为什么天空会变色？','narration':'光进入大气后会改变方向。','key':'阳光与大气','visual':'scattering',
               'spec':{'parameters':{'optical_path':'short'}}}
        p=director.validate({'title':'天空','scenes':[scene]})
        result=emotion_director.build({'emotional_goal':'从熟悉的景象逐渐理解原因',
            'beats':[{'scene_index':0,'kind':'发现','execution':'progressive'}]},p['scenes'])
        self.assertTrue(result['beats'][0]['applied'])
        self.assertEqual(p['scenes'][0]['spec']['animation']['keyframes'],[.08,.8])
        self.assertEqual(p['scenes'][0]['visual'],'scattering')

    def test_all_invalid_emotion_nodes_fall_back_without_losing_failure_details(self):
        from app.agents import emotion_director
        p=director.validate({'scenes':[self.scene()]})
        result=emotion_director.build({'emotional_goal':'等待','beats':[{'scene_index':99,'execution':'delayed_reveal'}]},p['scenes'])
        self.assertEqual(result['status'],'fallback')
        self.assertFalse(result['beats'][0]['applied'])
        self.assertIn('镜头',result['beats'][0]['actual'])

    def test_changed_visual_does_not_inherit_question_specific_reveal(self):
        from app.agents.animation_builder import preserve_audio_controls
        original={'visual':'question','spec':{'animation':{'narration_delay':1.3,'reveal_on_narration':True,'sound_effect':'soft_reveal'}}}
        revised={'visual':'flow','spec':{'animation':{}}}
        animation=preserve_audio_controls(original,revised)['spec']['animation']
        self.assertEqual(animation['narration_delay'],1.3)
        self.assertFalse(animation.get('reveal_on_narration',False))
        self.assertEqual(animation.get('sound_effect','none'),'none')
        explicit={'visual':'flow','spec':{'animation':{'sound_effect':'soft_reveal'}}}
        self.assertEqual(preserve_audio_controls(original,explicit)['spec']['animation']['sound_effect'],'soft_reveal')

    @patch('app.providers.siliconflow.chat')
    def test_full_optimizer_only_receives_valid_emotion_plan(self, chat):
        from app.agents import animation_builder
        chat.return_value=(json.dumps({'scenes':[self.scene()]}),{'role':'animation'})
        animation_builder.improve_plan('主题',[self.scene()],emotion_plan={'status':'generated','payoff':'认识真实规律'})
        self.assertIn('认识真实规律',chat.call_args.args[1][1]['content'])
        animation_builder.improve_plan('主题',[self.scene()],emotion_plan={'status':'stale','payoff':'过期意图'})
        self.assertNotIn('过期意图',chat.call_args.args[1][1]['content'])

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY':'test-only'})
    @patch('app.agents.animation_builder.improve_plan')
    @patch('app.providers.siliconflow.chat')
    def test_full_plan_optimizer_receives_emotional_arc(self, chat, improve):
        scene=self.scene()
        emotion={'emotional_goal':'好奇到理解','core_tension':'人与配对数量的差异','payoff':'解释二百五十三对',
                 'beats':[{'scene_index':0,'kind':'期待','execution':'delayed_reveal'}]}
        chat.side_effect=[(json.dumps({'title':'生日'},ensure_ascii=False),{'role':'writer'}),
                          (json.dumps({'title':'生日','scenes':[scene],'emotion_plan':emotion},ensure_ascii=False),{'role':'director_pro'})]
        improve.return_value=({'scenes':[scene]},{'role':'animation'})
        plan,_=director.generate('生日',mode='director')
        self.assertEqual(improve.call_args.kwargs['emotion_plan']['payoff'],'解释二百五十三对')
        self.assertTrue(plan['emotion_plan']['beats'][0]['applied'])

    def test_explicit_audio_control_changes_are_not_overwritten(self):
        from app.agents.animation_builder import preserve_audio_controls
        original={'spec':{'animation':{'narration_delay':1.3,'sound_effect':'soft_reveal','reveal_on_narration':True}}}
        revised={'spec':{'animation':{'narration_delay':0,'sound_effect':'none','reveal_on_narration':False}}}
        self.assertEqual(preserve_audio_controls(original,revised),revised)
        self.assertEqual(revised['spec']['animation']['sound_effect'],'none')
        self.assertEqual(revised['spec']['animation']['narration_delay'],0)

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY':'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_director_plan_optimization_preserves_omitted_audio_controls(self, chat):
        import copy
        initial=self.scene();initial['spec']['animation']={'narration_delay':1.3,'reveal_on_narration':True,'sound_effect':'soft_reveal'}
        refined=copy.deepcopy(initial);refined['heading']='优化画面';refined['spec']['animation']={}
        writer={'title':'主题'};plan={'title':'主题','scenes':[initial]}
        chat.side_effect=[(json.dumps(writer,ensure_ascii=False),{'role':'writer'}),
                          (json.dumps(plan,ensure_ascii=False),{'role':'director_pro'}),
                          (json.dumps({'scenes':[refined]},ensure_ascii=False),{'role':'animation'})]
        result,_=director.generate('主题',mode='director')
        settings=result['scenes'][0]['spec']['animation']
        self.assertEqual(settings['narration_delay'],1.3)
        self.assertTrue(settings['reveal_on_narration'])
        self.assertEqual(settings['sound_effect'],'soft_reveal')

    def test_optimization_omissions_preserve_existing_narration_and_sound_settings(self):
        import copy
        with tempfile.TemporaryDirectory(dir='data') as folder, patch.object(main,'DATA',Path(folder)):
            p=main.create(main.Create(example=True))
            settings={'narration_delay':1.2,'reveal_on_narration':True,'sound_effect':'soft_reveal'}
            p['scenes'][0]['spec']['animation'].update(settings);main.save(p['id'],p)
            revised=copy.deepcopy(p['scenes'][0])
            for key in settings:revised['spec']['animation'].pop(key)
            with patch.object(main.animation_builder,'improve',return_value=(revised,{'role':'animation'})):
                result=main.improve_scene(p['id'],0,main.SceneEdit())
            for key,value in settings.items():self.assertEqual(result['scenes'][0]['spec']['animation'][key],value)

    def test_local_improvement_invalidates_emotion_and_old_review(self):
        import copy
        with tempfile.TemporaryDirectory(dir='data') as folder, patch.object(main,'DATA',Path(folder)):
            p=main.create(main.Create(example=True))
            p['emotion_plan']={'status':'generated','beats':[{'scene_index':0,'kind':'期待'}]}
            p['reviews']={'0':{'summary':'old review'},'1':{'summary':'other scene'}}
            main.save(p['id'],p)
            revised=copy.deepcopy(p['scenes'][0]);revised['heading']='新的镜头'
            with patch.object(main.animation_builder,'improve',return_value=(revised,{'role':'animation'})) as improve:
                result=main.improve_scene(p['id'],0,main.SceneEdit())
            self.assertEqual(result['emotion_plan']['status'],'stale')
            self.assertTrue(result['reviews']['0']['stale'])
            self.assertNotIn('stale',result['reviews']['1'])
            self.assertEqual(improve.call_args.kwargs['emotion_beats'][0]['kind'],'期待')

    def test_new_asset_invalidates_review_of_old_image(self):
        from app.services import media_assets
        from PIL import Image
        with tempfile.TemporaryDirectory(dir='data') as folder:
            p=director.validate({'scenes':[self.scene()]})
            p['reviews']={'0':{'summary':'previous image'}}
            p['scenes'][0]['spec']['media'].update(kind='image',prompt='a studio')
            def download(url,target,*args):Image.new('RGB',(16,16)).save(target,format='PNG')
            with patch.object(main.siliconflow,'image',return_value='test-url'),patch.object(main.siliconflow,'download',side_effect=download):
                media_assets.prepare(p,Path(folder),lambda _:None,lambda:None)
            self.assertTrue(p['reviews']['0']['stale'])
            self.assertTrue(p['scenes'][0]['spec']['media']['asset_id'])

    def test_manual_edit_marks_changed_scene_review_stale(self):
        with tempfile.TemporaryDirectory(dir='data') as folder, patch.object(main,'DATA',Path(folder)):
            p=main.create(main.Create(example=True));p['reviews']={'0':{'summary':'old'}};main.save(p['id'],p)
            p['scenes'][0]['narration']='修改后的口播。'
            result=main.update_project(p['id'],p)
            self.assertTrue(result['reviews']['0']['stale'])

    def test_early_answer_in_heading_is_reported_without_changing_authored_text(self):
        from app.agents import emotion_director
        scene=self.scene();scene['heading']='答案是23'
        p=director.validate({'scenes':[scene]})
        result=emotion_director.build({'emotional_goal':'期待','beats':[{'scene_index':0,'kind':'期待',
            'execution':'delayed_reveal','pause_before_narration':1}]},p['scenes'])
        self.assertEqual(p['scenes'][0]['heading'],'答案是23')
        self.assertIn('标题已包含',result['beats'][0]['actual'])

    def test_invalid_emotion_scene_index_is_reported_and_not_applied(self):
        from app.agents import emotion_director
        p=director.validate({'scenes':[self.scene()]})
        raw={'emotional_goal':'好奇','beats':[{'scene_index':4,'kind':'揭晓','execution':'delayed_reveal'},
                                           {'scene_index':0,'kind':'期待','execution':'delayed_reveal'}]}
        result=emotion_director.build(raw,p['scenes'])
        self.assertEqual(len(result['beats']),2)
        self.assertIsNone(result['beats'][0]['scene_index'])
        self.assertFalse(result['beats'][0]['applied'])
        self.assertIn('镜头',result['beats'][0]['actual'])
        self.assertTrue(result['beats'][1]['applied'])

    def test_conflicting_beats_do_not_claim_both_timing_actions(self):
        from app.agents import emotion_director
        p=director.validate({'scenes':[self.scene()]})
        raw={'emotional_goal':'好奇','beats':[{'scene_index':0,'kind':'期待','execution':'delayed_reveal'},
                                             {'scene_index':0,'kind':'揭晓','execution':'delayed_reveal'}]}
        result=emotion_director.build(raw,p['scenes'])
        self.assertTrue(result['beats'][0]['applied'])
        self.assertFalse(result['beats'][1]['applied'])

    @patch('app.providers.siliconflow.image_data_url', return_value='data:image/jpeg;base64,AA==')
    @patch('app.providers.siliconflow.chat')
    def test_visual_review_receives_intent_without_claiming_emotion_detection(self, chat, image):
        from app.agents import visual_reviewer
        chat.return_value=('{}', {'role':'reviewer'})
        beat={'kind':'期待','knowledge_goal':'解释不同波长','execution':'progressive','applied':True}
        visual_reviewer.review(self.scene(),[Path('frame.jpg')], emotion_beats=[beat])
        prompt=chat.call_args.args[1][0]['content'][0]['text']
        self.assertIn('解释不同波长',prompt)
        self.assertIn('不能',prompt)
        self.assertIn('静帧',prompt)

    def test_light_renderer_obeys_reveal_timing(self):
        from app import render
        from app.agents import emotion_director
        p=director.validate({'title':'生日','scenes':[self.scene()]})
        emotion_director.build({'emotional_goal':'期待','beats':[{'scene_index':0,'kind':'期待','execution':'delayed_reveal'}]},p['scenes'])
        with patch.object(render, 'text_fit') as text:
            render.frame(p['scenes'][0],0,1,1.3,4)
            drawn=[call.args[2] for call in text.call_args_list]
        self.assertIn('100', drawn)
        self.assertNotIn('50', drawn)

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY':'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_smart_writer_emotional_direction_reaches_visual_director(self, chat):
        writer={'title':'蓝天','emotion_direction':{'core_tension':'熟悉天空颜色如何变化','payoff':'理解光的散射'}}
        visual={'title':'蓝天','scenes':[self.scene()], 'emotion_plan':{'emotional_goal':'从熟悉到发现',
                'beats':[{'scene_index':0,'kind':'curiosity','execution':'delayed_reveal'}]}}
        chat.side_effect=[(json.dumps(writer,ensure_ascii=False),{'role':'writer'}),(json.dumps(visual,ensure_ascii=False),{'role':'director'})]
        plan,usage=director.generate('天空为什么是蓝色',mode='smart')
        self.assertEqual(len(usage),2)
        self.assertIn('熟悉天空颜色如何变化',chat.call_args_list[1].args[1][1]['content'])
        self.assertEqual(plan['emotion_plan']['beats'][0]['applied'],True)

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY':'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_missing_or_unsupported_emotion_plan_is_honest_and_survives_project_edit(self, chat):
        obj={'title':'问题','scenes':[self.scene()]}
        chat.return_value=(json.dumps(obj,ensure_ascii=False),{'role':'writer'})
        plan,_=director.generate('主题',mode='fast')
        self.assertEqual(plan['emotion_plan']['status'],'fallback')
        self.assertTrue(any('情绪' in x for x in plan['warnings']))
        obj['emotion_plan']={'emotional_goal':'发现','beats':[{'scene_index':0,'kind':'reveal','execution':'unknown_effect'}]}
        chat.return_value=(json.dumps(obj,ensure_ascii=False),{'role':'writer'})
        plan,_=director.generate('主题',mode='fast')
        self.assertFalse(plan['emotion_plan']['beats'][0]['applied'])
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main,'DATA',Path(root)):
            p=main.create(main.Create(example=True)); p['emotion_plan']=plan['emotion_plan']; main.save(p['id'],p)
            result=main.update_project(p['id'],p)
            self.assertEqual(result['emotion_plan']['status'],'generated')
            self.assertFalse(result['emotion_plan']['beats'][0]['applied'])


if __name__=='__main__': unittest.main()
