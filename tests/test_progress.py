import contextlib
import io
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from nba_highlight_maker import processing as processing


class ProgressTests(unittest.TestCase):
    def test_streaming_receives_lines_and_keeps_process_failure_details(self):
        lines=[]
        processing.stream_run([sys.executable,'-u','-c',"print('first'); print('second')"],lines.append)
        self.assertEqual(lines,['first\n','second\n'])
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaisesRegex(RuntimeError,'decoder failed'):
            processing.stream_run([sys.executable,'-c',"import sys; print('decoder failed',file=sys.stderr); sys.exit(2)"],lines.append)

    def test_vision_updates_as_results_arrive_and_does_not_finish_on_error(self):
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'fake_vision'
            binary.write_text(f'#!{sys.executable}\nimport sys,json\nfor path in sys.stdin:\n print(json.dumps(dict(file=path.strip())),flush=True)\n')
            binary.chmod(0o755)
            with contextlib.redirect_stderr(io.StringIO()) as output:
                progress=processing.Progress('识别',2,'采样点')
                results=processing.vision([Path('a.jpg'),Path('b.jpg')],binary,progress)
            self.assertEqual([r['file'] for r in results],['a.jpg','b.jpg'])
            self.assertIn('100.0%',output.getvalue())
            binary.write_text(f'#!{sys.executable}\nimport json\nprint(json.dumps(dict(error="CVPixelBuffer failed")),flush=True)\n')
            with contextlib.redirect_stderr(io.StringIO()) as error_output,self.assertRaisesRegex(RuntimeError,'CVPixelBuffer'):
                progress=processing.Progress('识别',1,'采样点')
                processing.vision([Path('a.jpg')],binary,progress)
            self.assertNotIn('100.0%',error_output.getvalue())

    def test_clip_details_include_video_range_and_pending_reason(self):
        row=dict(player='doncic',kind='score',period=1,clock='PT11M27S',description='Doncic driving shot',
                 source='/tmp/Q1.mp4',start=61,end=72,status='keep',reason='',candidates='69',preview='/tmp/clip.mp4')
        text=processing.describe_clip(row,1,19)
        for part in ['[1/19]','Luka Doncic','运动战得分','11:27.000','01:01.000 → 01:12.000','11.000 秒','/tmp/clip.mp4']:
            self.assertIn(part,text)
        row.update(status='pending',reason='重复时钟',candidates='69;95')
        self.assertIn('重复时钟',processing.describe_clip(row,1,19))
        row.update(start='',end='',preview='')
        self.assertIn('未定位；未生成预览',processing.describe_clip(row,1,19))
        self.assertEqual(processing.format_video_time(59.9996),'01:00.000')
        self.assertEqual(processing.format_video_time(3661.12),'01:01:01.120')

    def test_terminal_refresh_colors_and_completion(self):
        output=io.StringIO()
        with patch.object(output,"isatty",return_value=True),contextlib.redirect_stderr(output),patch.object(processing.time,"monotonic",side_effect=[0,0,2]):
            progress=processing.Progress("OCR",10)
            progress.update(10)
        self.assertIn("\r\033[2K",output.getvalue())
        self.assertIn("\033[36m",output.getvalue())
        self.assertIn("100.0% 10/10",output.getvalue())
        self.assertIn("5.0 段/s ETA 00:00",output.getvalue())
        self.assertTrue(output.getvalue().endswith("\n"))

    def test_nonterminal_output_and_initial_unknown_eta(self):
        output=io.StringIO()
        with contextlib.redirect_stderr(output):
            progress=processing.Progress("OCR",10)
            progress.update(10)
        self.assertIn("ETA --:--",output.getvalue())
        self.assertNotIn("\r",output.getvalue())
        self.assertNotIn("\033",output.getvalue())

    def test_zero_work_and_update_throttling(self):
        output=io.StringIO()
        with contextlib.redirect_stderr(output),patch.object(processing.time,"monotonic",side_effect=[0,0,0.2,2]):
            progress=processing.Progress("OCR",10)
            progress.update(1)
            progress.update(10)
        self.assertEqual(len(output.getvalue().splitlines()),2)
        with contextlib.redirect_stderr(io.StringIO()) as empty:
            processing.Progress("OCR",0)
        self.assertIn("100.0% 0/0",empty.getvalue())

    def test_player_summary_counts_shared_and_combined_events(self):
        events=[dict(player='doncic',kind='score'),dict(player='doncic',kind='score+assist'),
                dict(player='doncic',kind='assist'),dict(player='reaves',kind='assist')]
        summary=processing.summarize_events(events)
        self.assertEqual(summary['doncic'],dict(scores=2,assists=2,clips=3))
        self.assertEqual(summary['reaves'],dict(scores=0,assists=1,clips=1))
        self.assertEqual(processing.summarize_events([])['reaves'],dict(scores=0,assists=0,clips=0))


if __name__=="__main__":unittest.main()
