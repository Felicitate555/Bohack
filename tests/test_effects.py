import tempfile
import unittest
import wave
from pathlib import Path
import numpy as np
from app import director

class EffectsTests(unittest.TestCase):
    def scene(self, effect='soft_reveal'):
        return director.validate({'scenes':[{'heading':'问题','narration':'答案是二十三。','key':'猜测','visual':'question',
          'spec':{'parameters':{'candidates':['50','23']},'animation':{'sound_effect':effect,'narration_delay':2,'reveal_on_narration':True}}}]})['scenes'][0]

    def test_sound_effect_is_opt_in_bounded_and_hits_reveal(self):
        from app import sound_effects
        with tempfile.TemporaryDirectory(dir='data') as d:
            root=Path(d)
            self.assertIsNone(sound_effects.write_track([self.scene('none')],[4],root/'none.wav'))
            path=sound_effects.write_track([self.scene()],[4],root/'cue.wav')
            with wave.open(str(path),'rb') as f:
                rate=f.getframerate(); x=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(float)/32768
            self.assertEqual(rate,44100)
            self.assertLess(float(np.max(np.abs(x[:int(1.99*rate)]))),.0001)
            self.assertGreater(float(np.max(np.abs(x[int(2*rate):int(2.3*rate)]))),.005)
            self.assertLess(float(np.max(np.abs(x))),.15)
            self.assertEqual(self.scene('unknown')['spec']['animation']['sound_effect'],'none')

    def test_cue_follows_actual_beat_bound_reveal_frame(self):
        from app import sound_effects
        scene=self.scene(); anim=scene['spec']['animation']
        anim.update(narration_delay=0,reveal_on_narration=False,motion='linear',keyframes=[0,.4],beat_binding=True)
        when=sound_effects.frame_reveal_time(scene,5,[2.2,3.7],15,'browser')
        self.assertAlmostEqual(when,1.2,places=5)
        when=sound_effects.frame_reveal_time(scene,5,[],12,'light')
        self.assertAlmostEqual(when,1.0,places=5)

    def test_emotional_reveal_requests_only_supported_optional_effect(self):
        from app.agents import emotion_director
        scene=self.scene('none')
        plan=emotion_director.build({'emotional_goal':'期待','beats':[{'scene_index':0,'kind':'揭晓',
            'execution':'delayed_reveal','pause_before_narration':2,'sound_effect':'soft_reveal'}]},[scene])
        self.assertTrue(plan['beats'][0]['applied'])
        self.assertEqual(scene['spec']['animation']['sound_effect'],'soft_reveal')
        self.assertIn('音效',plan['beats'][0]['actual'])

    def test_unsupported_requested_sound_is_reported(self):
        from app.agents import emotion_director
        scene=self.scene('none')
        result=emotion_director.build({'emotional_goal':'期待','beats':[{'scene_index':0,'kind':'揭晓',
            'execution':'delayed_reveal','sound_effect':'explosion'}]},[scene])
        self.assertEqual(scene['spec']['animation']['sound_effect'],'none')
        self.assertIn('未应用',result['beats'][0]['actual'])

    def test_light_mixed_scenes_preserve_audio_stream(self):
        import subprocess
        from app import render
        with tempfile.TemporaryDirectory(dir='data') as d:
            root=Path(d)
            render.render({'scenes':[self.scene(),self.scene('none')]},root,False,lambda _:None)
            data=subprocess.run(['ffmpeg','-v','error','-i',str(root/'video.mp4'),'-ac','1','-ar','44100','-f','f32le','-'],capture_output=True,check=True).stdout
            x=np.frombuffer(data,dtype='<f4')
            self.assertGreater(len(x),8*44100)
            self.assertGreater(float(np.max(np.abs(x[88200:101430]))),.005)
            self.assertLess(float(np.max(np.abs(x[6*44100:7*44100]))),.001)

    def test_light_render_contains_optional_effect_without_speech(self):
        import subprocess
        from app import render
        with tempfile.TemporaryDirectory(dir='data') as d:
            root=Path(d); plan={'scenes':[self.scene()]}
            duration=render.render(plan,root,False,lambda _:None)
            self.assertGreaterEqual(duration,4)
            data=subprocess.run(['ffmpeg','-v','error','-i',str(root/'video.mp4'),'-ac','1','-ar','44100','-f','f32le','-'],capture_output=True,check=True).stdout
            x=np.frombuffer(data,dtype='<f4')
            self.assertLess(float(np.max(np.abs(x[int(.5*44100):int(1.5*44100)]))),.001)
            self.assertGreater(float(np.max(np.abs(x[int(2*44100):int(2.3*44100)]))),.005)

    def test_effect_only_mix_creates_audio_at_reveal(self):
        import subprocess
        from app import sound_effects,browser_render
        with tempfile.TemporaryDirectory(dir='data') as d:
            root=Path(d);base=root/'base.mp4';out=root/'out.mp4'
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=size=160x90:rate=15:duration=4','-c:v','libx264','-pix_fmt','yuv420p',str(base)],check=True)
            track=sound_effects.write_track([self.scene()],[4],root/'cue.wav')
            browser_render.mux_audio(base,out,[],[4],None,.18,effects=track)
            pcm=subprocess.run(['ffmpeg','-v','error','-i',str(out),'-ac','1','-ar','44100','-f','f32le','-'],capture_output=True,check=True).stdout
            x=np.frombuffer(pcm,dtype='<f4');self.assertLess(np.max(np.abs(x[22050:66000])),.001)
            self.assertGreater(np.max(np.abs(x[88200:101430])),.005)

if __name__=='__main__': unittest.main()
