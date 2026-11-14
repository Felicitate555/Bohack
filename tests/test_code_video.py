import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import code_video


class CodeVideoTests(unittest.TestCase):
    def test_new_smart_and_director_projects_require_code_sandbox(self):
        from fastapi import HTTPException
        from app import main
        with patch.object(code_video, 'check_available', side_effect=RuntimeError('Docker 不可用')), patch.object(main, 'launch_planning'):
            for mode in ('smart', 'director', 'code'):
                with self.subTest(mode=mode), self.assertRaises(HTTPException):
                    main.start_planning(main.Create(topic='星空', creative_mode=mode))

    def test_pipeline_flag_preserves_existing_projects(self):
        from app import main
        self.assertTrue(main.uses_code({'creative_mode':'smart','render_pipeline':'code'}))
        self.assertTrue(main.uses_code({'creative_mode':'code'}))
        self.assertFalse(main.uses_code({'creative_mode':'smart'}))
        self.assertFalse(main.uses_code({'creative_mode':'fast'}))

    def test_smart_code_planning_uses_free_director_with_smart_model(self):
        from app import director
        script={'title':'潮汐','narrative':[]}
        reply=json.dumps({'title':'潮汐','scenes':[{'heading':'月球','narration':'引力让海水受影响。','visual_brief':'月球与海水在两侧形成动态关系'}]},ensure_ascii=False)
        with patch.object(director.siliconflow,'api_key',return_value='configured'), patch.object(director.knowledge_writer,'write',return_value=(script,{})), patch.object(director.siliconflow,'chat',return_value=(reply,{})) as chat:
            plan,_=director.generate('潮汐',mode='code_smart')
        self.assertEqual(chat.call_args.args[0],'director')
        self.assertIn('不要预设已有组件',chat.call_args.args[1][0]['content'])
        self.assertIn('动态关系',plan['scenes'][0]['visual_brief'])

    def test_review_requests_one_visual_repair_and_keeps_verified_clip(self):
        from app import code_render, director
        scene=director.validate({'scenes':[{'heading':'月球','narration':'潮汐。','visual_brief':'海水变化'}]})['scenes'][0]
        project={'topic':'潮汐','scenes':[scene]}
        with tempfile.TemporaryDirectory() as folder:
            def concat(args):Path(args[-1]).write_bytes(b'joined')
            with patch.object(code_video,'check_available'), patch.object(code_video,'generate_code',side_effect=[('first',{}),('repaired',{})]) as generate, patch.object(code_video,'render_sandbox',side_effect=[b'first video',b'repaired video']), patch.object(code_render,'review_clip',return_value=({'needs_revision':True,'issues':[{'severity':'high','problem':'主体不可见','suggestion':'增大主体'}]},{})), patch.object(code_render.render,'run',side_effect=concat):
                code_render.render_video(project,Path(folder)/"run",False,lambda _:None)
            self.assertEqual((Path(folder)/'run'/'scene_0.mp4').read_bytes(),b'repaired video')
            self.assertIn('主体不可见',generate.call_args.kwargs['error'])

    def test_auto_render_starts_after_background_planning_without_browser(self):
        from app import main, director
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        plan={'id':'6f40b449-103e-4568-9f85-108aef944122','topic':'潮汐','title':'潮汐','scenes':[],
              'mode':'ai','creative_mode':'smart','render_pipeline':'code',
              'planning_job':{},'auto_render':{'voice':True,'quality':'preview','voice_provider':'edge'}}
        result=director.validate({'topic':'潮汐','scenes':[{'heading':'月球','narration':'海水变化。','visual_brief':'动态海面'}]})
        with tempfile.TemporaryDirectory() as folder, patch.object(main,'DATA',Path(folder)), patch.object(main.threading,'Thread',ImmediateThread), patch.object(main.director,'generate',return_value=(result,[])), patch.object(main,'start_render') as start:
            main.launch_planning(plan['id'],plan)
            self.assertEqual(main.read_project(plan['id'])['planning_job']['status'],'done')
            self.assertEqual(start.call_args.args[0],plan['id'])
            self.assertTrue(start.call_args.args[1].voice)
            self.assertEqual(start.call_args.args[1].quality,'preview')

    def test_review_repair_failure_preserves_first_clip_and_warning(self):
        from app import code_render, director
        scene=director.validate({'scenes':[{'heading':'潮汐','narration':'海水变化。','visual_brief':'海面运动'}]})['scenes'][0]
        project={'topic':'潮汐','scenes':[scene],'warnings':[]}
        with tempfile.TemporaryDirectory() as folder:
            def concat(args):Path(args[-1]).write_bytes(b'joined')
            with patch.object(code_video,'check_available'), patch.object(code_video,'generate_code',side_effect=[('first',{}),('repair',{})]), patch.object(code_video,'render_sandbox',side_effect=[b'first',RuntimeError('repair failed')]), patch.object(code_render,'review_clip',return_value=({'needs_revision':True,'issues':[{'severity':'high','problem':'empty'}]},{})), patch.object(code_render.render,'run',side_effect=concat):
                code_render.render_video(project,Path(folder)/"run",False,lambda _:None)
            self.assertEqual((Path(folder)/'run'/'scene_0.mp4').read_bytes(),b'first')
            self.assertEqual(json.loads((Path(folder)/'run'/'scene_0_review.json').read_text(encoding='utf-8'))['repair'],'failed_original_preserved')
            self.assertTrue(project['warnings'])

    def test_successful_scene_is_reused_only_for_identical_timeline_and_brief(self):
        from app import code_render, director
        scene=director.validate({'scenes':[{'heading':'潮汐','narration':'海水变化。','visual_brief':'海面运动'}]})['scenes'][0]
        project={'topic':'潮汐','scenes':[scene]}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def concat(args):Path(args[-1]).write_bytes(b'joined')
            with patch.object(code_video,'check_available'), patch.object(code_video,'generate_code',return_value=('source',{})) as generate, patch.object(code_video,'render_sandbox',return_value=b'video') as sandbox, patch.object(code_render,'review_clip',return_value=({'needs_revision':False,'issues':[]},{})) as review, patch.object(code_render.render,'run',side_effect=concat):
                code_render.render_video(project,root/'run1',False,lambda _:None)
                code_render.render_video(project,root/'run2',False,lambda _:None)
                self.assertEqual(generate.call_count,1)
                self.assertEqual(sandbox.call_count,1)
                self.assertEqual(review.call_count,1)
                next((root/'code_cache').glob('*/scene.mp4')).write_bytes(b'corrupted')
                code_render.render_video(project,root/'run3',False,lambda _:None)
                self.assertEqual(generate.call_count,2)
                project['scenes'][0]['visual_brief']='旋转的地球和海面'
                code_render.render_video(project,root/'run4',False,lambda _:None)
                self.assertEqual(generate.call_count,3)

    def test_sandbox_command_has_no_network_or_mounts(self):
        command = code_video.sandbox_command('vibelab-test')
        self.assertIn('--network=none', command)
        self.assertIn('--read-only', command)
        self.assertIn('--cap-drop=ALL', command)
        self.assertIn('--security-opt=no-new-privileges', command)
        self.assertFalse(any(x.startswith('--volume') or x in ('-v', '--mount') for x in command))
        self.assertEqual(command[-1], 'vibelab-code-renderer:local')

    def test_code_generation_receives_story_and_repairs_error(self):
        scene = {'heading': '开场', 'narration': '新的问题', 'visual_brief': '星群聚合'}
        responses = [('bad code', {'role': 'animation'}), ('def render_frame(t, duration, w, h):\n    pass', {'role': 'animation'})]
        with patch.object(code_video.siliconflow, 'chat', side_effect=responses) as chat:
            first, _ = code_video.generate_code('测试主题', scene)
            code, _ = code_video.generate_code('测试主题', scene, previous=first, error='第 1 帧错误')
        self.assertIn('render_frame', code)
        self.assertIn('星群聚合', json.dumps(chat.call_args_list[0].args, ensure_ascii=False))
        self.assertIn('第 1 帧错误', json.dumps(chat.call_args_list[1].args, ensure_ascii=False))

    def test_scene_validation_preserves_free_visual_brief(self):
        from app import director
        brief = '星群由暗至亮，逐渐汇聚成一颗星'
        scene = {'heading': '开场', 'narration': '新的问题', 'visual_brief': brief}
        self.assertEqual(director.validate({'scenes': [scene]})['scenes'][0]['visual_brief'], brief)

    def test_render_repairs_failed_code_and_saves_working_source(self):
        from app import code_render, director
        scene = director.validate({'scenes': [{'heading': '开场', 'narration': '新的问题',
                                                'visual_brief': '星群聚合'}]})['scenes'][0]
        project = {'topic': '星星', 'scenes': [scene], 'target_seconds': None}
        with tempfile.TemporaryDirectory() as folder:
            def concat(args):
                Path(args[-1]).write_bytes(b'joined-video')
            with patch.object(code_video, 'check_available'), \
                 patch.object(code_video, 'generate_code', side_effect=[('broken', {}), ('fixed', {})]) as generate, \
                 patch.object(code_video, 'render_sandbox', side_effect=[RuntimeError('bad frame'), b'video']), \
                 patch.object(code_render, 'review_clip', return_value=({'needs_revision':False, 'issues':[]}, {})), \
                 patch.object(code_render.render, 'run', side_effect=concat):
                code_render.render_video(project, Path(folder)/"run", False, lambda message: None)
            self.assertEqual((Path(folder) / 'run' / 'scene_0.py').read_text(encoding='utf-8'), 'fixed')
            self.assertTrue((Path(folder) / 'run' / 'video.mp4').is_file())
            self.assertIn('bad frame', generate.call_args.kwargs['error'])

    def test_free_director_does_not_require_a_template(self):
        from app import director
        script = {'title': '星星', 'narrative': []}
        answer = json.dumps({'title': '星星', 'scenes': [{'heading': '开场',
             'narration': '看星空。', 'visual_brief': '让星群汇聚成星云'}]}, ensure_ascii=False)
        with patch.object(director.siliconflow, 'api_key', return_value='configured'), \
             patch.object(director.knowledge_writer, 'write', return_value=(script, {})), \
             patch.object(director.siliconflow, 'chat', return_value=(answer, {})) as chat:
            plan, _ = director.generate('星星', mode='code')
        self.assertEqual(plan['scenes'][0]['visual_brief'], '让星群汇聚成星云')
        self.assertIn('不要预设已有组件', chat.call_args.args[1][0]['content'])

    def test_offline_example_does_not_enable_generated_code(self):
        from app import main
        with tempfile.TemporaryDirectory() as folder, patch.object(main, 'DATA', Path(folder)):
            project = main.create(main.Create(example=True, creative_mode='code'))
        self.assertEqual(project['mode'], 'example')
        self.assertNotEqual(project['creative_mode'], 'code')

    def test_planning_checks_sandbox_before_any_model_call(self):
        from fastapi import HTTPException
        from app import main
        with patch.object(code_video, 'check_available', side_effect=RuntimeError('Docker 不可用')), \
             patch.object(main.director, 'generate') as generate:
            with self.assertRaises(HTTPException) as error:
                main.start_planning(main.Create(topic='星空', creative_mode='code'))
        self.assertIn('Docker', error.exception.detail)
        generate.assert_not_called()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
    def test_trusted_worker_produces_a_video(self):
        payload = {'code': 'from PIL import Image\ndef render_frame(t, duration, w, h):\n'
                           '    return Image.new("RGB", (w, h), (10, 20, 30))\n',
                   'width': 960, 'height': 540, 'fps': 15, 'duration': .4, 'captions': []}
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'TEMP': folder, 'TMP': folder, 'TMPDIR': folder}
            worker = Path(__file__).resolve().parent.parent / 'sandbox' / 'worker.py'
            result = subprocess.run([sys.executable, str(worker)], input=json.dumps(payload).encode(),
                                    capture_output=True, env=env, timeout=30, check=True)
            video = Path(folder) / 'result.mp4'
            video.write_bytes(result.stdout)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                    '-show_entries', 'stream=width,height', '-of', 'json', str(video)],
                                   capture_output=True, text=True, check=True)
            stream = json.loads(probe.stdout)['streams'][0]
            self.assertEqual((stream['width'], stream['height']), (960, 540))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
    def test_trusted_code_runs_through_video_assembly(self):
        from app import code_render, director
        scene = director.validate({'scenes': [{'heading': '开场', 'narration': '看星空。',
                                                'visual_brief': '星群聚合'}]})['scenes'][0]
        code = 'from PIL import Image\ndef render_frame(t, duration, w, h):\n    return Image.new("RGB", (w,h), (15,20,30))'
        worker = Path(__file__).resolve().parent.parent / 'sandbox' / 'worker.py'
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'TEMP': folder, 'TMP': folder, 'TMPDIR': folder}
            def run_trusted(payload, **kwargs):
                return subprocess.run([sys.executable, str(worker)], input=json.dumps(payload).encode(),
                                      capture_output=True, env=env, timeout=45, check=True).stdout
            with patch.object(code_video, 'check_available'), \
                 patch.object(code_video, 'generate_code', return_value=(code, {})), \
                 patch.object(code_video, 'render_sandbox', side_effect=run_trusted), \
                 patch.object(code_render, 'review_clip', return_value=({'needs_revision':False, 'issues':[]}, {})):
                seconds = code_render.render_video({'topic': '星星', 'scenes': [scene]}, Path(folder)/'run', False, lambda _: None)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                    '-of', 'json', str(Path(folder) / 'run' / 'video.mp4')],
                                   capture_output=True, text=True, check=True)
            self.assertGreaterEqual(seconds, 4)
            self.assertGreater(float(json.loads(probe.stdout)['format']['duration']), 3.9)
            self.assertIn('看星空', (Path(folder) / 'run' / 'subtitles.srt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
