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
