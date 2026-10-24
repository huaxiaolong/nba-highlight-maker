import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nba_highlight_maker import processing as nh


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要 FFmpeg')
class RenderClipTests(unittest.TestCase):
    def test_logo_visible_only_when_requested_and_audio_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4'
            nh.run(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=640x360:r=25',
                    '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','0.8',
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-y',source])
            for watermark in [False,True]:
                with self.subTest(watermark=watermark):
                    output=root/f'{watermark}.mp4'
                    nh.render_clip(source,0,0.4,output,watermark=watermark)
                    self.assertAlmostEqual(nh.probe(output),0.4,delta=0.05)
                    def crop(y):
                        return subprocess.run(['ffmpeg','-v','error','-i',str(output),'-vf',
                                               f'crop=300:120:1588:{y}','-frames:v','1','-pix_fmt','rgb24',
                                               '-f','rawvideo','pipe:1'],capture_output=True,check=True).stdout
                    logo_pixels=crop(32)
                    self.assertEqual(len(logo_pixels),300*120*3)
                    self.assertEqual(sum(logo_pixels)>100000,watermark)
                    self.assertLess(sum(crop(160)),1000,'Logo 下方不应遗留旧位置的标志')

    def test_silent_opening_gets_audio_but_match_audio_is_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'silent.mov';output=root/'opening.mp4'
            nh.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=320x180:r=25',
                    '-t','0.4','-c:v','libx264','-pix_fmt','yuv420p','-y',source])
            with self.assertRaisesRegex(ValueError,'音频'):nh.probe(source)
            self.assertAlmostEqual(nh.probe(source,require_audio=False),0.4,delta=0.05)
            nh.render_clip(source,0,0.4,output,allow_silent=True)
            self.assertAlmostEqual(nh.probe(output),0.4,delta=0.05)

    def test_missing_logo_preserves_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'old.mp4';output.write_bytes(b'old export')
            with patch.object(nh,'ROOT',root),patch.object(nh,'run') as run:
                with self.assertRaisesRegex(ValueError,'Logo'):
                    nh.render_clip(root/'source.mp4',0,1,output,watermark=True)
                run.assert_not_called()
            self.assertEqual(output.read_bytes(),b'old export')
