import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from app import director
from app.providers import siliconflow
from app.services import media_assets
from app import browser_render, main


class PipelineTests(unittest.TestCase):
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

    @patch.dict('os.environ', {'SF_PRICE_IMAGE_CNY':'0.30'}, clear=False)
    def test_media_budget_and_local_asset(self):
        scene = {'spec':{'media':{'kind':'image','prompt':'blue sky','asset_id':'','request_id':'','submission_state':''}}}
        with tempfile.TemporaryDirectory(dir='data') as root:
            plan = {'creative_mode':'smart','scenes':[scene]}
            with patch.object(siliconflow, 'image', return_value='https://example.test/a'), patch.object(siliconflow, 'download', side_effect=lambda url, path, *a: Image.new('RGB',(4,4)).save(path,format='PNG')):
                media_assets.prepare(plan, Path(root), .2, lambda s:None, lambda:None)
                self.assertFalse(scene['spec']['media']['asset_id'])
                media_assets.prepare(plan, Path(root), .3, lambda s:None, lambda:None)
            ident = scene['spec']['media']['asset_id']
            self.assertTrue((Path(root)/'assets'/(ident+'.png')).is_file())
            self.assertEqual(plan['usage_events'][0]['actual_cny'], None)

    @patch('app.providers.siliconflow.post', return_value=b'audio')
    def test_speech_request(self, post):
        self.assertEqual(siliconflow.speech('你好','anna',1.2), b'audio')
        args, kwargs = post.call_args
        self.assertEqual(args[0], '/audio/speech')
        self.assertEqual(args[1]['voice'], 'FunAudioLLM/CosyVoice2-0.5B:anna')
        self.assertTrue(kwargs['binary'])

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

    @patch.dict('os.environ', {'SF_PRICE_IMAGE_CNY':'0.30'}, clear=False)
    def test_comparison_labels_only_media_cost(self):
        with tempfile.TemporaryDirectory(dir='data') as root, patch.object(main, 'DATA', Path(root)):
            p = main.create(main.Create(example=True, creative_mode='smart'))
            p['scenes'][0]['spec']['media']['kind'] = 'image'
            main.save(p['id'], p)
            comparison = main.cost_plan(p['id'])
            self.assertEqual(comparison['local_only']['media_cny'], 0)
            self.assertEqual(comparison['mixed']['media_cny'], .3)
            self.assertIn('仅媒体', comparison['scope'])


if __name__ == '__main__':
    unittest.main()
