import tempfile
import json
import wave
import threading
import unittest
import sys
import types
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from app import director
from app.providers import siliconflow
from app.services import media_assets, speech
from app import browser_render, main, audio, render, scene_spec, style_profile


class PipelineTests(unittest.TestCase):
    def test_sentence_speech_uses_measured_lengths_and_bounds_calls(self):
        import numpy as np
        calls = []
        def fake_create(scenes, outdir, provider, voice, speed, update, report, indices=None):
            calls.append(scenes[0]['narration'])
            duration = .4 if len(calls) == 1 else .9
            with wave.open(str(outdir / 'voice_0.mp3'), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                f.writeframes(np.zeros(round(16000*duration), dtype='<i2').tobytes())
        scenes = [{'narration':'第一句。第二句。'}]
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(speech, 'create', side_effect=fake_create):
            root = Path(root)
            speech.create_segmented(scenes, root, 'edge', 'anna', 1, lambda s:None, lambda e:None)
            cues = json.loads((root/'voice_0.cues.json').read_text(encoding='utf-8'))
            self.assertEqual(calls, ['第一句。','第二句。'])
            self.assertAlmostEqual(cues[0]['end'], .4, delta=.01)
            self.assertAlmostEqual(cues[1]['start'], .55, delta=.01)
            self.assertAlmostEqual(cues[1]['end'], 1.45, delta=.02)
            self.assertTrue((root/'voice_0.mp3').is_file())
            self.assertEqual(speech.read_cues(root/'voice_0.cues.json', scenes[0]['narration'], 2), cues)
            with self.assertRaises(ValueError):
                speech.read_cues(root/'voice_0.cues.json', '不同文本。', 2)
        self.assertGreater(len(speech.sentence_chunks('短句。' * 33)), 32)
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(speech, 'create') as create:
            with self.assertRaisesRegex(ValueError, '最多 32 段'):
                speech.create_segmented([{'narration':'短句。' * 33}], Path(root), 'edge', 'anna', 1, lambda s:None, lambda e:None)
            create.assert_not_called()
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(speech, 'create', side_effect=fake_create):
            speech.create_segmented([{'narration':'单句。'}], Path(root), 'edge', 'anna', 1, lambda s:None, lambda e:None)
            self.assertTrue((Path(root)/'voice_0.mp3').is_file())

    def test_measured_caption_cues_control_srt_and_hold_between_sentences(self):
        cues = [{'text':'短句。','start':0,'end':.4}, {'text':'较长的第二句。','start':.55,'end':1.45}]
        result = list(render.caption_timeline('短句。较长的第二句。', 2, 1.45, cues))
        self.assertEqual(result, [('短句。', 0, .55), ('较长的第二句。', .55, 2)])

    def test_edge_voice_does_not_silently_replace_an_unsupported_choice(self):
        module=types.ModuleType('edge_tts')
        class FakeCommunication:
            def __init__(self,*args,**kwargs): pass
            async def save(self,path): Path(path).write_bytes(b'fake')
        module.Communicate=FakeCommunication
        with tempfile.TemporaryDirectory(dir='data') as root, patch.dict(sys.modules, {'edge_tts':module}):
            with self.assertRaises(siliconflow.ProviderError):
                speech.create([{'narration':'测试'}],Path(root),'edge','bella',1,lambda s:None,lambda e:None)

    def test_failed_sentence_speech_reuses_completed_segment_on_retry(self):
        import numpy as np
        calls = []
        def fake_create(scenes, outdir, provider, voice, speed, update, report, indices=None):
            calls.append(scenes[0]['narration'])
            if len(calls) == 2: raise ValueError('temporary failure')
            with wave.open(str(outdir / 'voice_0.mp3'), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                f.writeframes(np.zeros(8000, dtype='<i2').tobytes())
        with tempfile.TemporaryDirectory(dir='data') as folder, patch.object(speech, 'create', side_effect=fake_create):
            root=Path(folder); first=root/'first'; second=root/'second'; first.mkdir(); second.mkdir()
            scenes=[{'narration':'第一句。第二句。'}]
            with self.assertRaises(ValueError):
                speech.create_segmented(scenes, first, 'edge', 'anna', 1, lambda s:None, lambda e:None, cache_dir=root/'cache')
            speech.create_segmented(scenes, second, 'edge', 'anna', 1, lambda s:None, lambda e:None, cache_dir=root/'cache')
            self.assertEqual(calls, ['第一句。','第二句。','第二句。'])
        with patch.object(speech, 'model', side_effect=['model-A','model-B']):
            first_key=speech.voice_cache_key('文字','siliconflow','anna',1,'sentence')
            second_key=speech.voice_cache_key('文字','siliconflow','anna',1,'sentence')
            self.assertNotEqual(first_key,second_key)

    def test_style_profile_survives_project_edit_and_drives_render_colors(self):
        custom = style_profile.normalize({'name':'测试风格','palette':{'background':'#102030','accent':'#ff4400'}})
        self.assertEqual(custom['palette']['background'], '#102030')
        self.assertEqual(style_profile.normalize({'palette':{'accent':'not-a-color'}})['palette']['accent'], style_profile.DEFAULT['palette']['accent'])
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)):
            plan = main.create(main.Create(example=True))
            plan['preview_job'] = {'status':'done', 'index':0, 'id':'a'*12}
            main.save(plan['id'], plan)
            plan['style_profile'] = custom
            updated = main.update_project(plan['id'], plan)
            self.assertEqual(updated['style_profile']['palette']['accent'], '#ff4400')
            self.assertEqual(updated['scenes'][0]['spec']['style']['accent'], '#ff4400')
            self.assertEqual(updated['preview_job']['status'], 'stale')
            scene = updated['scenes'][0]
            image = render.frame(scene, 0, 1, 2, 4, custom)
            self.assertEqual(image.getpixel((1, 1)), (16, 32, 48))
            updated['scenes'][0]['spec']['media'].update({'kind':'image','asset_id':'a'*32})
            updated = main.update_project(plan['id'], updated)
            updated['style_profile']['palette']['accent'] = '#00aabb'
            updated = main.update_project(plan['id'], updated)
            self.assertEqual(updated['scenes'][0]['spec']['media']['asset_id'], 'a'*32)
            self.assertTrue(any('已有素材' in warning for warning in updated['warnings']))

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY':'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_style_profile_is_in_director_prompt(self, chat):
        chat.return_value = (json.dumps({'title':'测试','scenes':[{'heading':'主题','narration':'解释','key':'重点','visual':'question'}]}, ensure_ascii=False), {'role':'writer'})
        custom = style_profile.normalize({'name':'自定义测试','palette':{'accent':'#ff4400'}})
        director.generate('主题', mode='fast', profile=custom)
        prompt = chat.call_args.args[1][0]['content']
        self.assertIn('自定义测试', prompt)
        self.assertIn('#ff4400', prompt)

    def test_profile_sets_scene_defaults_without_overriding_explicit_choices(self):
        profile = style_profile.normalize({'motion':{'default_transition':'cut','beat_emphasis':False}})
        scene = {'heading':'测试','narration':'解释','key':'重点','visual':'question'}
        result = director.validate({'scenes':[scene], 'style_profile':profile})['scenes'][0]['spec']
        self.assertEqual(result['transition'], 'cut')
        self.assertFalse(result['animation']['beat_binding'])
        scene['spec']={'transition':'fade','animation':{'beat_binding':True}}
        result = director.validate({'scenes':[scene], 'style_profile':profile})['scenes'][0]['spec']
        self.assertEqual(result['transition'], 'fade')
        self.assertTrue(result['animation']['beat_binding'])

    def test_media_prompt_uses_project_style_without_replacing_scene_request(self):
        scene = {'spec': {'media': {'kind':'image','prompt':'a brass telescope','asset_id':'','request_id':'','submission_state':''}}}
        plan = {'creative_mode':'smart', 'scenes':[scene], 'style_profile':style_profile.normalize({'media_prompt':'warm copper and teal'})}
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(siliconflow, 'image', return_value='https://example.test/a') as image, patch.object(siliconflow, 'download', side_effect=lambda url, path, *a: Image.new('RGB',(4,4)).save(path,format='PNG')):
            media_assets.prepare(plan, Path(root), lambda s:None, lambda:None)
        prompt = image.call_args.args[0]
        self.assertIn('a brass telescope', prompt)
        self.assertIn('warm copper and teal', prompt)

    def test_distinct_probability_requires_valid_assumptions(self):
        scene = {'heading':'不重复概率', 'narration':'逐次抽取', 'key':'概率', 'visual':'distinct',
                 'spec':{'parameters':{'categories':365,'draws':23}}}
        plan = director.validate({'scenes':[scene]})
        self.assertEqual(plan['scenes'][0]['visual'], 'distinct')
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['categories'], 365)
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['draws'], 23)
        self.assertFalse(director.visual_issues(director.validate(plan)))
        for categories, draws in ((None,23),(365,0),(365,2.5),(2,4),(-1,3),(365,True)):
            scene['spec']['parameters']={'categories':categories,'draws':draws}
            self.assertTrue(director.visual_issues(director.validate({'scenes':[scene]})))
        self.assertAlmostEqual(scene_spec.distinct_probability(365, 23), .4927027657, places=9)
        self.assertEqual(scene_spec.distinct_probability(2, 3), 0)

    def test_offline_example_explains_probability_in_light_renderer(self):
        plan = director.validate(director.example())
        scene = next(s for s in plan['scenes'] if s['visual'] == 'distinct')
        self.assertEqual((scene['spec']['parameters']['categories'], scene['spec']['parameters']['draws']), (365, 23))
        with patch.object(render, 'text_fit', wraps=render.text_fit) as drawn:
            render.frame(scene, 0, 1, 4, 4)
        values = [str(c.args[2]) for c in drawn.call_args_list]
        self.assertTrue(any('49.27%' in value for value in values))
        self.assertTrue(any('50.73%' in value for value in values))
        self.assertTrue(any('独立' in value and '等概率' in value for value in values))
        self.assertFalse(any('忽略其他条件' in value for value in values))

    def test_light_renderer_preserves_user_text_and_missing_data_error(self):
        scene = director.validate({'scenes':[{'heading':'测试','narration':'说明','key':'重点','detail':'用户输入：粒子数量为示意','visual':'dots','spec':{'parameters':{'count':3}}}]})['scenes'][0]
        with patch.object(render, 'text_fit', wraps=render.text_fit) as drawn:
            render.frame(scene, 0, 1, 4, 4)
        self.assertTrue(any('用户输入：粒子数量为示意' == str(c.args[2]) for c in drawn.call_args_list))
        scene['visual']='distinct';scene['spec']['parameters']['categories']=None
        with patch.object(render, 'text_fit', wraps=render.text_fit) as drawn:
            render.frame(scene, 0, 1, 4, 4)
        self.assertTrue(any('请填写有效' in str(c.args[2]) for c in drawn.call_args_list))

    def test_light_network_uses_validated_nodes_edges_and_motion(self):
        item = {'heading':'节点','narration':'关系','key':'网络','visual':'network',
                'spec':{'parameters':{'count':3,'edges':[[0,1],[1,2]]},
                        'animation':{'node_motions':[{'node':1,'start':0,'end':1,'from_x':0,'from_y':0,'to_x':60,'to_y':-30,'easing':'linear'}]}}}
        scene = director.validate({'scenes':[item]})['scenes'][0]
        before = render.network_geometry(scene, 0, 4)
        after = render.network_geometry(scene, 4, 4)
        self.assertEqual(len(before['points']), 3)
        self.assertEqual(after['edges'], [[0,1],[1,2]])
        self.assertEqual(after['shown'], 2)
        self.assertAlmostEqual(after['points'][1][0] - before['points'][1][0], 60)
        self.assertAlmostEqual(after['points'][1][1] - before['points'][1][1], -30)
        item['spec'] = {'parameters': {'count':23}}
        complete = render.network_geometry(director.validate({'scenes':[item]})['scenes'][0], 4, 4)
        self.assertEqual(complete['shown'], 253)

    def test_light_renderer_uses_declared_flow_and_comparison_data(self):
        item = {'heading':'流程','narration':'说明','key':'流程','visual':'flow',
                'spec':{'parameters':{'labels':['输入','处理','输出','反馈']}}}
        scene = director.validate({'scenes':[item]})['scenes'][0]
        with patch.object(render, 'text_fit', wraps=render.text_fit) as drawn:
            render.frame(scene, 0, 1, 4, 4)
        values = [str(c.args[2]) for c in drawn.call_args_list]
        self.assertTrue(all(label in values for label in ('输入','处理','输出','反馈')))
        item.update(visual='compare', key='比例')
        item['spec']={'parameters':{'value':37,'secondary_value':63,'labels':['A','B']}}
        scene = director.validate({'scenes':[item]})['scenes'][0]
        with patch.object(render, 'text_fit', wraps=render.text_fit) as drawn:
            render.frame(scene, 0, 1, 4, 4)
        values = [str(c.args[2]) for c in drawn.call_args_list]
        self.assertTrue(any('37%' in value for value in values))

    def test_light_renderer_refuses_unsupported_visuals(self):
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)):
            plan = main.create(main.Create(example=True))
            plan['scenes'][0]['visual'] = 'scattering'
            main.save(plan['id'], plan)
            with self.assertRaises(main.HTTPException) as error:
                main.start_render(plan['id'], main.RenderRequest(engine='light'))
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn('浏览器', error.exception.detail)

    def test_offline_example_can_start_light_render(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(main.threading, 'Thread', ImmediateThread), patch.object(render, 'render', return_value=8):
            plan = main.create(main.Create(example=True))
            main.start_render(plan['id'], main.RenderRequest(engine='light'))
            self.assertEqual(main.read_project(plan['id'])['job']['status'], 'done')

    def test_node_motion_is_validated_and_round_trips(self):
        motion = {'node': 1, 'start': .2, 'end': .8, 'from_x': 0, 'from_y': 0,
                  'to_x': 60, 'to_y': -30, 'easing': 'linear', 'beat_binding': True}
        scene = {'heading':'节点', 'narration':'节点移动', 'key':'关系', 'visual':'network',
                 'spec':{'parameters':{'count':3, 'edges':[[0,1]]}, 'animation':{'node_motions':[motion]}}}
        plan = director.validate({'scenes':[scene]})
        self.assertEqual(plan['scenes'][0]['spec']['animation']['node_motions'], [motion])
        self.assertEqual(director.validate(plan)['scenes'][0]['spec']['animation']['node_motions'], [motion])
        self.assertFalse(director.visual_issues(plan))
        for broken in ({**motion, 'node': 3}, {**motion, 'to_x': 1000}, {**motion, 'end': .1}):
            scene['spec']['animation']['node_motions'] = [broken]
            self.assertTrue(director.visual_issues(director.validate({'scenes':[scene]})))
        scene['spec']['animation']['node_motions'] = [motion, motion]
        self.assertTrue(director.visual_issues(director.validate({'scenes':[scene]})))

    def test_cancel_during_frames_stops_encoder(self):
        plan = director.validate(director.example())
        plan['scenes'] = plan['scenes'][:1]
        for engine in ('browser', 'light'):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory(dir='data') as root:
                calls = []
                def update(message): pass
                def check():
                    calls.append(1)
                    if len(calls) >= 3: raise render.RenderCancelled()
                update.check_cancel = check
                proc = MagicMock(); proc.stderr.read.return_value = b''; proc.wait.return_value = 0
                page = MagicMock(); page.screenshot.return_value = b'frame'
                browser = MagicMock(); browser.new_page.return_value = page
                context = MagicMock(); context.__enter__.return_value.chromium.launch.return_value = browser
                module = types.ModuleType('playwright.sync_api'); module.sync_playwright = lambda: context
                with patch.dict(sys.modules, {'playwright.sync_api': module}), patch.object(browser_render, 'available', return_value=True), patch.object(render.shutil, 'which', return_value='ffmpeg'), patch.object(render, 'frame', return_value=MagicMock()), patch('subprocess.Popen', return_value=proc):
                    with self.assertRaises(render.RenderCancelled):
                        if engine == 'browser': browser_render.render(plan, Path(root), False, update)
                        else: render.render(plan, Path(root), False, update)
                proc.kill.assert_called_once()

    def test_cancel_render_waits_for_worker_and_preserves_last_video(self):
        started, proceed = threading.Event(), threading.Event()
        def renderer(plan, outdir, voice, update, *args, **kwargs):
            started.set()
            if not proceed.wait(5): raise RuntimeError('test timeout')
            update.check_cancel()
            raise AssertionError('cancelled render must not continue')
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(browser_render, 'available', return_value=True), patch.object(browser_render, 'render', side_effect=renderer):
            plan = main.create(main.Create(example=True)); pid = plan['id']
            old = '123456abcdef'
            folder = main.project_dir(pid) / old; folder.mkdir()
            (folder / 'video.mp4').write_bytes(b'old video')
            plan['job'] = {'status':'done', 'run_id':old, 'duration':4, 'voice':False}
            main.save(pid, plan)
            try:
                main.start_render(pid, main.RenderRequest())
                self.assertTrue(started.wait(3))
                main.cancel_render(pid)
                state = main.read_project(pid)['job']
                self.assertEqual(state['status'], 'running')
                self.assertIn('取消', state['message'])
                self.assertEqual(Path(main.export(pid, 'mp4').path), folder / 'video.mp4')
                proceed.set()
                deadline = time.monotonic() + 5
                while main.render_lock.locked() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(main.render_lock.locked())
                self.assertEqual(main.read_project(pid)['job']['status'], 'cancelled')
                self.assertEqual(Path(main.export(pid, 'mp4').path), folder / 'video.mp4')
            finally:
                proceed.set()

    def test_render_lock_released_when_task_cannot_start(self):
        for preview in (False, True):
            for failure in ('save', 'thread'):
                with self.subTest(preview=preview, failure=failure), tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(browser_render, 'available', return_value=True):
                    plan = main.create(main.Create(example=True))
                    target = lambda: main.preview_scene(plan['id'], 0) if preview else main.start_render(plan['id'], main.RenderRequest())
                    try:
                        if failure == 'save':
                            with patch.object(main, 'save', side_effect=OSError('disk error')):
                                with self.assertRaises(OSError): target()
                        else:
                            with patch.object(main.threading, 'Thread') as thread:
                                thread.return_value.start.side_effect = RuntimeError('thread error')
                                with self.assertRaises(RuntimeError): target()
                        self.assertFalse(main.render_lock.locked())
                        if failure == 'thread':
                            job = main.read_project(plan['id'])['preview_job' if preview else 'job']
                            self.assertEqual(job['status'], 'error')
                    finally:
                        if main.render_lock.locked(): main.render_lock.release()

    def test_narration_segments_do_not_accumulate_codec_delay(self):
        import numpy as np
        rate = 22050
        with tempfile.TemporaryDirectory(dir='data') as folder:
            root = Path(folder)
            base = root / 'base.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=64x64:rate=15:duration=9', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(base)], check=True, capture_output=True, timeout=20)
            voices = []
            for i, frequency in enumerate((440, 660, 880)):
                voice = root / f'voice_{i}.mp3'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={frequency}:duration=0.8:sample_rate=44100', str(voice)], check=True, capture_output=True, timeout=20)
                voices.append(voice)
            output = root / 'output.mp4'
            browser_render.mux_audio(base, output, voices, [3, 3, 3], None, .18)
            pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:a', '-f', 'f32le', '-ac', '1', '-ar', str(rate), '-'], check=True, capture_output=True, timeout=20).stdout
            samples = np.frombuffer(pcm, dtype='<f4')
            offsets = []
            for i in range(3):
                start = int(i * 3 * rate)
                for j in range(start, start + rate // 2, 110):
                    if np.sqrt(np.mean(samples[j:j+110] ** 2)) > .008:
                        offsets.append(j / rate - 3 * i)
                        break
            self.assertEqual(len(offsets), 3)
            self.assertLess(max(offsets) - min(offsets), .03)
            music = root / 'music.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=4:sample_rate=44100', str(music)], check=True, capture_output=True, timeout=20)
            mixed = root / 'mixed.mp4'
            browser_render.mux_audio(base, mixed, voices, [3, 3, 3], music, .18)
            pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(mixed), '-map', '0:a', '-f', 'f32le', '-ac', '1', '-ar', str(rate), '-'], check=True, capture_output=True, timeout=20).stdout
            samples = np.frombuffer(pcm, dtype='<f4')
            third = samples[int(6.2 * rate):int(6.6 * rate)]
            freqs = np.fft.rfftfreq(len(third), 1 / rate)
            spectrum = np.abs(np.fft.rfft(third))
            self.assertAlmostEqual(freqs[np.argmax(spectrum)], 880, delta=3)
            music_only = root / 'music_only.mp4'
            browser_render.mux_audio(base, music_only, [], [3, 3, 3], music, .18)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=duration', '-of', 'default=nw=1:nk=1', str(music_only)], check=True, capture_output=True, text=True, timeout=20)
            self.assertAlmostEqual(float(probe.stdout.strip()), 9, delta=.05)

    def test_scattering_optical_path_survives_validation(self):
        scene = {'heading':'光线', 'narration':'光线穿过介质', 'key':'散射', 'visual':'scattering', 'spec':{'parameters':{'optical_path':'long'}}}
        plan = director.validate({'scenes':[scene]})
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['optical_path'], 'long')
        self.assertEqual(director.validate(plan)['scenes'][0]['spec']['parameters']['optical_path'], 'long')
        scene['spec']['parameters']['optical_path'] = 'unknown'
        self.assertEqual(director.validate({'scenes':[scene]})['scenes'][0]['spec']['parameters']['optical_path'], 'short')
        scene['spec']['parameters']['optical_path'] = ['long']
        self.assertEqual(director.validate({'scenes':[scene]})['scenes'][0]['spec']['parameters']['optical_path'], 'short')

    def test_music_is_ducked_during_narration(self):
        import numpy as np
        rate = 16000
        t = np.arange(rate * 4) / rate
        voice = np.where(t < 2, .5 * np.sin(2 * np.pi * 440 * t), 0)
        music = .3 * np.sin(2 * np.pi * 220 * t)
        with tempfile.TemporaryDirectory(dir='data') as root:
            root = Path(root)
            for name, samples in [('voice.wav', voice), ('music.wav', music)]:
                with wave.open(str(root / name), 'wb') as f:
                    f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
                    f.writeframes((samples * 32767).astype('<i2').tobytes())
            # Inspect the ducked music alone so narration does not affect the measurement.
            filt = browser_render.music_mix_filter(4, 1, True)
            result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(root / 'voice.wav'), '-i', str(root / 'music.wav'), '-filter_complex', filt, '-map', '[a]', '-f', 'f32le', '-ac', '1', '-ar', str(rate), '-'], capture_output=True, check=True, timeout=20)
            samples = np.frombuffer(result.stdout, dtype='<f4')
            # The voice is at 440 Hz and music at 220 Hz; measure only the music band.
            def amplitude(chunk):
                return abs(np.dot(chunk, np.exp(-2j * np.pi * 220 * np.arange(len(chunk)) / rate))) / len(chunk)
            loud = amplitude(samples[rate//2:rate*3//2])
            quiet = amplitude(samples[rate*5//2:rate*3])
            self.assertLess(loud, quiet * .7)

    def test_failed_render_preserves_previous_export_and_retry_cache(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        calls = []
        def renderer(plan, outdir, *args, **kwargs):
            calls.append(args[-1])
            if len(calls) == 2:
                self.assertTrue(Path(main.export(plan['id'], 'mp4').path).is_file())
                raise RuntimeError('模拟编码失败')
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / 'video.mp4').write_bytes(b'video')
            (outdir / 'subtitles.srt').write_text('subtitle', encoding='utf-8')
            return 4.0
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(main.threading, 'Thread', ImmediateThread), patch.object(browser_render, 'available', return_value=True), patch.object(browser_render, 'render', side_effect=renderer):
            plan = main.create(main.Create(example=True))
            pid = plan['id']
            main.start_render(pid, main.RenderRequest())
            original = Path(main.export(pid, 'mp4').path)
            main.start_render(pid, main.RenderRequest())
            self.assertEqual(main.read_project(pid)['job']['status'], 'error')
            self.assertIn('模拟编码失败', main.read_project(pid)['job']['message'])
            self.assertEqual(Path(main.export(pid, 'mp4').path), original)
            main.start_render(pid, main.RenderRequest())
            self.assertEqual(calls[2], original.parent.name)
            self.assertNotEqual(Path(main.export(pid, 'mp4').path), original)

    def test_example_edit_render_and_download_paths(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        def render_mock(plan, outdir, *args, **kwargs):
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / 'video.mp4').write_bytes(b'video')
            (outdir / 'subtitles.srt').write_text('subtitle', encoding='utf-8')
            return 4.0
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(main.threading, 'Thread', ImmediateThread), patch.object(browser_render, 'available', return_value=True), patch.object(browser_render, 'render', side_effect=render_mock):
            plan = main.create(main.Create(example=True))
            plan['scenes'][0]['heading'] = '已编辑'
            main.update_project(plan['id'], plan)
            main.start_render(plan['id'], main.RenderRequest())
            self.assertEqual(main.read_project(plan['id'])['scenes'][0]['heading'], '已编辑')
            for kind in ['mp4', 'srt', 'json']:
                self.assertTrue(Path(main.export(plan['id'], kind).path).is_file())

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    def test_background_planning_exposes_state_while_worker_runs(self):
        started, proceed, finished = threading.Event(), threading.Event(), threading.Event()
        original_save = main.save
        def generate(topic, seconds, mode, progress, checkpoint, profile=None):
            progress('测试阶段')
            started.set()
            if not proceed.wait(3): raise RuntimeError('测试等待超时')
            return director.validate(director.example()), []
        def save(pid, plan):
            original_save(pid, plan)
            if (plan.get('planning_job') or {}).get('status') == 'done': finished.set()
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(main, 'save', side_effect=save), patch.object(director, 'generate', side_effect=generate):
            task = main.start_planning(main.Create(topic='测试'))
            self.assertTrue(started.wait(2))
            state = main.read_project(task['id'])['planning_job']
            self.assertEqual(state['status'], 'running')
            self.assertEqual(state['message'], '测试阶段')
            proceed.set()
            self.assertTrue(finished.wait(2))

    def test_long_storyboard_supports_more_than_eight_scenes(self):
        scene = {'heading':'解释','narration':'一段解释','key':'重点','visual':'flow'}
        self.assertEqual(len(director.validate({'scenes':[dict(scene) for _ in range(12)]})['scenes']), 12)
        with self.assertRaises(ValueError):
            director.validate({'scenes':[dict(scene) for _ in range(17)]})

    def test_custom_network_edges_validate_and_survive_roundtrip(self):
        scene = {'heading':'关系', 'narration':'三个节点', 'key':'关系', 'visual':'network',
                 'spec': {'parameters': {'count':3, 'edges':[[0,1],[1,2],[1,0]]}}}
        plan = director.validate({'scenes':[scene]})
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['edges'], [[0,1],[1,2]])
        self.assertEqual(director.visual_issues(director.validate(plan)), [])
        scene['spec']['parameters']['edges'] = [[0,4]]
        broken = director.validate({'scenes':[scene]})
        self.assertTrue(director.visual_issues(director.validate(broken)))

    def test_caption_ranges_follow_text_length_and_include_final_hold(self):
        spans = list(render.caption_ranges(['短句', '长长长长长长长长'], 12, 10))
        self.assertAlmostEqual(spans[0][1], 2.0)
        self.assertAlmostEqual(spans[1][0], 2.0)
        self.assertEqual(spans[1][1], 12)

    def test_beat_analysis_detects_generated_audio_onsets(self):
        import numpy as np
        sample_rate = 11025
        samples = np.zeros(sample_rate * 6, dtype='<i2')
        for position in np.arange(.5, 5.6, .5):
            start = int(position * sample_rate)
            samples[start:start+80] = (np.sin(np.arange(80) * .6) * 24000).astype('<i2')
        with tempfile.TemporaryDirectory(dir='data') as root:
            path = Path(root) / 'beats.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(sample_rate)
                stream.writeframes(samples.tobytes())
            timeline = audio.analyze(path)
            mp3 = Path(root) / 'beats.mp3'
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), str(mp3)], check=True, capture_output=True, timeout=20)
            compressed = audio.analyze(mp3)
        self.assertGreaterEqual(len(timeline['beats']), 8)
        self.assertTrue(any(abs(t - 1.0) < .1 for t in timeline['beats']))
        self.assertGreater(timeline['duration'], 5)
        self.assertEqual(compressed['rhythm_status'], 'estimated')
        self.assertGreaterEqual(len(compressed['beats']), 8)
        self.assertLess(abs(compressed['bpm'] - timeline['bpm']), 6)

    def test_unrhythmic_audio_does_not_produce_binding_beats(self):
        import numpy as np
        sample_rate = 11025
        t = np.arange(sample_rate * 8) / sample_rate
        signals = [np.zeros(len(t)), .25 * np.sin(2 * np.pi * 440 * t),
                   np.random.default_rng(7).normal(0, .1, len(t))]
        with tempfile.TemporaryDirectory(dir='data') as root:
            for index, signal in enumerate(signals):
                path = Path(root) / f'nonrhythmic_{index}.wav'
                with wave.open(str(path), 'wb') as stream:
                    stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(sample_rate)
                    stream.writeframes((np.clip(signal, -1, 1) * 32767).astype('<i2').tobytes())
                timeline = audio.analyze(path)
                self.assertIsNone(timeline['bpm'])
                self.assertEqual(timeline['beats'], [])
                self.assertEqual(timeline['strong_beats'], [])
                self.assertEqual(timeline['rhythm_status'], 'unreliable')

    def test_old_or_unreliable_beat_analysis_is_not_used_for_binding(self):
        self.assertEqual(browser_render.usable_beats({'beats':[1, 2], 'strong_beats':[2]}), ([], []))
        self.assertEqual(browser_render.usable_beats({'rhythm_status':'unreliable', 'beats':[1], 'strong_beats':[1]}), ([], []))
        self.assertEqual(browser_render.usable_beats({'rhythm_status':'estimated', 'beats':[1, 2], 'strong_beats':[2]}), ([1, 2], [2]))

    def test_growth_and_animation_events_are_validated(self):
        item = {'heading':'复利','narration':'固定增长率示意','key':'增长','visual':'growth','spec':{
            'parameters':{'principal':100,'rate':10,'periods':3},
            'animation':{'events':[{'property':'translate_x','start':0.1,'end':0.6,'from':-30,'to':0,'beat_binding':True},
                                   {'property':'scale','start':0,'end':1,'from':1,'to':10},
                                   {'property':'code','start':0,'end':1,'from':0,'to':1}]}}}
        plan = director.validate({'scenes':[item]})
        self.assertEqual(director.visual_issues(plan), [])
        self.assertEqual(len(plan['scenes'][0]['spec']['animation']['events']), 1)
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['periods'], 3)
        item['spec']['parameters']['periods'] = 3.5
        self.assertTrue(director.visual_issues(director.validate({'scenes':[item]})))

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    def test_planning_checkpoint_survives_later_failure(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        def generator(topic, seconds, mode, progress, checkpoint, profile=None):
            partial = director.validate(director.example())
            checkpoint(partial, [{'role': 'director'}])
            raise ValueError('动画规划中断')
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(main.threading, 'Thread', ImmediateThread), patch.object(director, 'generate', side_effect=generator):
            task = main.start_planning(main.Create(topic='测试', creative_mode='director'))
            saved = main.read_project(task['id'])
            self.assertEqual(saved['planning_job']['status'], 'error')
            self.assertIn('动画规划中断', saved['planning_job']['message'])
            self.assertTrue(saved['scenes'])

    def test_interrupted_planning_becomes_recoverable_error(self):
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)):
            pid = '11541154-1154-4154-8154-115411541154'
            main.save(pid, {'planning_job': {'status': 'running'}, 'scenes': []})
            self.assertEqual(main.read_project(pid)['planning_job']['status'], 'error')

    def test_retry_reuses_script_and_preserves_previous_usage(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        script = {'title': '已经保存的稿'}
        def generate(topic, seconds, mode, progress, checkpoint, initial_script=None, profile=None):
            self.assertEqual(initial_script, script)
            return director.validate(director.example()), [{'role': 'director'}]
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)), patch.object(main.threading, 'Thread', ImmediateThread), patch.object(director, 'generate', side_effect=generate):
            pid = '22542254-2254-4254-8254-225422542254'
            main.save(pid, {'id': pid, 'topic': '概率', 'creative_mode': 'director', 'scenes': [], 'knowledge_script': script,
                            'usage_events': [{'role': 'writer'}], 'planning_job': {'status': 'error'}})
            main.retry_planning(pid)
            saved = main.read_project(pid)
            self.assertEqual(saved['planning_job']['status'], 'done')
            self.assertEqual([e['role'] for e in saved['usage_events']], ['writer', 'director'])

    def test_retry_refuses_to_overwrite_existing_storyboard(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)):
            pid = '33543354-3354-4354-8354-335433543354'
            main.save(pid, {'id': pid, 'scenes': [{'heading': '保留'}], 'planning_job': {'status': 'error'}})
            with self.assertRaises(HTTPException): main.retry_planning(pid)
            self.assertEqual(main.read_project(pid)['scenes'][0]['heading'], '保留')

    def test_legacy_visuals_and_missing_chart_data(self):
        def scene(visual, heading, action, **spec):
            return {'heading': heading, 'narration': '解释。', 'key': 'internal_id', 'detail': '', 'visual': visual,
                    'spec': {'animation': {'action': action}, **spec}}
        plan = director.validate({'scenes': [
            scene('curve', '微小差异', '两条曲线逐渐分叉'),
            scene('geometry', '洛伦兹', '展示双翼轨迹'),
            scene('flow', '变化放大', '扰动逐级放大'),
            scene('timeline', '两种状态', '', parameters={'candidates': ['相同', '不同']}),
            scene('curve', '数据', ''),
        ]})
        self.assertEqual([s['visual'] for s in plan['scenes']], ['divergence', 'lorenz', 'amplify', 'contrast', 'curve'])
        self.assertEqual(plan['scenes'][0]['key'], '微小差异')
        self.assertEqual(len(director.visual_issues(plan)), 1)

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_writer_director_and_math_route(self, chat):
        chat.side_effect = [
            ('{"title":"概率","key_facts":["23人"]}', {'role':'writer','usage':{'total_tokens':10}}),
            ('{"title":"概率","scenes":[{"heading":"23人概率","narration":"比较任意两人","key":"23","detail":"配对","visual":"network","spec":{"parameters":{"count":23},"media":{"kind":"video","prompt":"room"}}}]}', {'role':'director','usage':{'total_tokens':20}}),
        ]
        plan, usage = director.generate('生日悖论', 60, 'smart')
        self.assertEqual(len(usage), 2)
        self.assertEqual(plan['scenes'][0]['spec']['parameters']['count'], 23)
        self.assertEqual(plan['scenes'][0]['spec']['media']['kind'], 'animation')
        director_prompt = chat.call_args_list[1].args[1][0]['content']
        self.assertIn('animation.action 只记录设计意图', director_prompt)
        self.assertIn('不能指定或声称知道实际节拍', director_prompt)

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_unrenderable_chart_is_replanned(self, chat):
        chat.side_effect = [
            ('{"scenes":[{"heading":"变化","narration":"解释变化","key":"变化","visual":"curve"}]}', {'role':'writer'}),
            ('{"scenes":[{"heading":"变化","narration":"解释变化","key":"变化","visual":"flow","spec":{"parameters":{"labels":["开始","结果"]}}}]}', {'role':'writer'}),
        ]
        plan, events = director.generate('变化', 30, 'fast')
        self.assertEqual(plan['scenes'][0]['visual'], 'flow')
        self.assertEqual(len(events), 2)
        self.assertIn('不要编造数据', chat.call_args_list[1].args[1][-1]['content'])

    def test_all_selected_images_are_generated_without_price_configuration(self):
        scenes = [{'spec': {'media': {'kind': 'image', 'prompt': 'blue sky', 'asset_id': '', 'request_id': '', 'submission_state': ''}}} for _ in range(2)]
        with tempfile.TemporaryDirectory(dir='data') as root:
            plan = {'creative_mode': 'smart', 'scenes': scenes}
            with patch.object(siliconflow, 'image', return_value='https://example.test/a') as image, patch.object(siliconflow, 'download', side_effect=lambda url, path, *a: Image.new('RGB', (4, 4)).save(path, format='PNG')):
                media_assets.prepare(plan, Path(root), lambda s: None, lambda: None)
                self.assertEqual(image.call_count, 2)
                self.assertEqual(image.call_args.kwargs['role'], 'image')
            for scene in scenes:
                self.assertTrue((Path(root) / 'assets' / (scene['spec']['media']['asset_id'] + '.png')).is_file())

    def test_fast_mode_uses_configurable_fast_image_role(self):
        scene = {'spec': {'media': {'kind': 'image', 'prompt': 'blue sky', 'asset_id': '', 'request_id': '', 'submission_state': ''}}}
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(siliconflow, 'image', return_value='https://example.test/a') as image, patch.object(siliconflow, 'download', side_effect=lambda url, path, *a: Image.new('RGB', (4, 4)).save(path, format='PNG')):
            media_assets.prepare({'creative_mode': 'fast', 'scenes': [scene]}, Path(root), lambda s: None, lambda: None)
            self.assertEqual(image.call_args.kwargs['role'], 'image_fast')

    @patch('app.providers.siliconflow.post', return_value=b'audio')
    def test_speech_request(self, post):
        self.assertEqual(siliconflow.speech('你好','anna',1.2), b'audio')
        args, kwargs = post.call_args
        self.assertEqual(args[0], '/audio/speech')
        self.assertEqual(args[1]['voice'], 'FunAudioLLM/CosyVoice2-0.5B:anna')
        self.assertTrue(kwargs['binary'])

    @patch('app.providers.siliconflow.post', return_value=b'audio')
    @patch('app.providers.siliconflow.model', return_value='FunAudioLLM/CosyVoice2-0.5B')
    def test_cosyvoice_receives_documentary_instruction_as_control(self, model, post):
        siliconflow.speech('正文。','claire',1.0,instruction='自然、清晰地讲解。')
        self.assertEqual(post.call_args.args[1]['input'], '自然、清晰地讲解。<|endofprompt|>正文。')

    @patch('app.providers.siliconflow.post', return_value=b'audio')
    def test_moss_narration_has_speaker_tag(self, post):
        with patch('app.providers.siliconflow.model', return_value='fnlp/MOSS-TTSD-v0.5'):
            siliconflow.speech('这是旁白。', 'anna', 1, 'speech_alt')
        self.assertEqual(post.call_args.args[1]['input'], '[S1]这是旁白。')

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    @patch('app.providers.siliconflow.requests.post')
    def test_provider_reports_actionable_failure_without_response_body(self, request):
        request.return_value = MagicMock(ok=False, status_code=403, headers={'x-siliconcloud-trace-id': 'trace_123'})
        with self.assertRaises(siliconflow.ProviderError) as exc:
            siliconflow.post('/audio/speech', {})
        self.assertIn('HTTP 403', str(exc.exception))
        self.assertIn('模型权限', str(exc.exception))
        self.assertIn('trace_123', str(exc.exception))

    def test_unchanged_scene_reuses_rendered_clip(self):
        plan = director.validate(director.example())
        plan['scenes'] = plan['scenes'][:1]
        page = MagicMock()
        page.screenshot.return_value = b'frame'
        browser = MagicMock()
        browser.new_page.return_value = page
        context = MagicMock()
        context.__enter__.return_value.chromium.launch.return_value = browser
        module = types.ModuleType('playwright.sync_api')
        module.sync_playwright = lambda: context
        def encoder(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'clip')
            proc = MagicMock()
            proc.stderr.read.return_value = b''
            proc.wait.return_value = 0
            return proc
        def mux(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'video')
        with tempfile.TemporaryDirectory(dir='data') as root, patch.dict(sys.modules, {'playwright.sync_api': module}), patch.object(browser_render, 'available', return_value=True), patch.object(browser_render, 'browser_path', return_value='chromium'), patch.object(browser_render.subprocess, 'Popen', side_effect=encoder) as popen, patch.object(browser_render, 'run', side_effect=mux):
            first = 'a'*12
            browser_render.render(plan, Path(root)/first, False, lambda s:None)
            self.assertEqual(popen.call_count, 1)
            browser_render.render(plan, Path(root)/('b'*12), False, lambda s:None, previous_run=first)
            self.assertEqual(popen.call_count, 1)
            plan['style_profile'] = {'palette':{'accent':'#ff4400'}}
            browser_render.render(plan, Path(root)/('c'*12), False, lambda s:None, previous_run=first)
            self.assertEqual(popen.call_count, 2, 'style change must invalidate rendered frames')

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    @patch('app.providers.siliconflow.requests.post')
    def test_flagship_stream_stops_when_total_deadline_is_exceeded(self, post):
        response = post.return_value.__enter__.return_value
        response.ok = True
        response.iter_content.return_value = iter([b':', b' ', b'\n', b' '])
        with patch('app.providers.siliconflow.time.monotonic', side_effect=[0, 1, 2, 601, 602]):
            with self.assertRaisesRegex(siliconflow.ProviderError, '总时限'):
                siliconflow.chat('director_pro', [{'role': 'user', 'content': '示例'}])
        post.return_value.__exit__.assert_called_once()

    def test_flagship_stream_rejects_oversized_unterminated_line(self):
        response = MagicMock()
        response.iter_content.return_value = iter([b'a', b'b', b'c', b'd'])
        with self.assertRaisesRegex(siliconflow.ProviderError, '大小限制'):
            list(siliconflow.stream_lines(response, siliconflow.time.monotonic() + 10, max_bytes=3))

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    @patch('app.providers.siliconflow.requests.post')
    def test_flagship_stream_and_usage(self, post):
        response = post.return_value.__enter__.return_value
        response.ok = True
        stream = '\n'.join([
            'data: ' + json.dumps({'choices': [{'delta': {'reasoning_content': 'hidden'}, 'finish_reason': None}]}),
            'data: ' + json.dumps({'choices': [{'delta': {'content': '{"title":"好"}'}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 17}}, ensure_ascii=False),
            'data: [DONE]',
        ]) + '\n'
        response.iter_content.return_value = (bytes([b]) for b in stream.encode('utf-8'))
        result, event = siliconflow.chat('director_pro', [{'role': 'user', 'content': '示例'}], max_tokens=500)
        self.assertEqual(json.loads(result)['title'], '好')
        self.assertEqual(event['usage']['total_tokens'], 17)
        self.assertTrue(post.call_args.kwargs['json']['stream'])

    @patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'test-only'})
    @patch('app.providers.siliconflow.chat')
    def test_director_pipeline_has_animation_pass(self, chat):
        original = {'heading':'解释','narration':'真实旁白','key':'重点','visual':'flow','spec':{'parameters':{'labels':['开始','结束']}}}
        improved = {**original, 'narration':'不应覆盖', 'visual':'amplify'}
        chat.side_effect = [(json.dumps({'title':'稿'}, ensure_ascii=False), {'role':'writer'}),
                            (json.dumps({'title':'片','scenes':[original]}, ensure_ascii=False), {'role':'director_pro'}),
                            (json.dumps({'scenes':[improved]}, ensure_ascii=False), {'role':'animation'})]
        plan, events = director.generate('主题', 60, 'director')
        self.assertEqual([e['role'] for e in events], ['writer', 'director_pro', 'animation'])
        self.assertEqual(plan['scenes'][0]['visual'], 'amplify')
        self.assertEqual(plan['scenes'][0]['narration'], '真实旁白')


if __name__ == '__main__':
    unittest.main()
