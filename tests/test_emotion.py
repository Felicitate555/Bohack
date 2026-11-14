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
