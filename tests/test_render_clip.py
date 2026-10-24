import shutil
import csv
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nba_highlight_maker import processing as nh


class FrameRateTests(unittest.TestCase):
    def test_exact_frame_rates_and_invalid_metadata(self):
        for raw,expected in [('60000/1001\n','60000/1001'),('30000/1001','30000/1001'),('25/1','25')]:
            with self.subTest(raw=raw),patch.object(nh,'run',return_value=raw):
                self.assertEqual(nh.probe_frame_rate(Path('source.mp4')),expected)
        for raw in ['', '0/0', '0/1', '-25', 'N/A', 'nan']:
            with self.subTest(raw=raw),patch.object(nh,'run',return_value=raw),self.assertRaisesRegex(ValueError,'帧率'):
                nh.probe_frame_rate(Path('source.mp4'))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要 FFmpeg')
class RenderClipTests(unittest.TestCase):
    def test_real_export_keeps_fractional_match_rate_across_opening_concat(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,rate in [('intro','25'),('match','60000/1001')]:
                nh.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=black:s=320x180:r={rate}',
                        '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','0.4',
                        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-y',root/(name+'.mp4')])
            row=dict.fromkeys(nh.FIELDS,'')
            row.update(event_id='1',period='1',player='doncic',source=str(root/'match.mp4'),start='0',end='0.4',status='keep')
            review=root/'review.csv'
            with review.open('w',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
            with patch('nba_highlight_maker.cover.generate_cover',return_value=root/'cover.png'):
                nh.export(SimpleNamespace(review=str(review),prepend=[str(root/'intro.mp4')],watermark=False))
            output=root/'highlights.mp4'
            self.assertEqual(Fraction(nh.probe_frame_rate(output)),Fraction('60000/1001'))
            self.assertAlmostEqual(nh.probe(output),0.8,delta=0.15)
            nh.run(['ffmpeg','-v','error','-i',output,'-f','null','-'])

    def test_source_fractional_rate_is_preserved_and_opening_can_match_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for source_rate,override in [('60000/1001',None),('25','60000/1001')]:
                with self.subTest(source_rate=source_rate):
                    source=root/'source.mp4';output=root/'clip.mp4'
                    nh.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=black:s=320x180:r={source_rate}',
                            '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','0.8',
                            '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-y',source])
                    nh.render_clip(source,0,0.4,output,frame_rate=override)
                    self.assertEqual(Fraction(nh.probe_frame_rate(output)),Fraction('60000/1001'))
                    self.assertAlmostEqual(nh.probe(output),0.4,delta=0.06)

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
