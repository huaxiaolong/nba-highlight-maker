import tempfile
import json
import hashlib
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from nba_highlight_maker import download as nd
from nba_highlight_maker import processing as nh


def samples(periods):
    return [dict(t=i*100+j*20,period=p,clock=700-j*20 if p<=4 else 280-j*20,
                 home=None,away=None,raw=dict(period=f"{p}TH"))
            for i,p in enumerate(periods) for j in range(5)]


class ClassificationTests(unittest.TestCase):
    def test_single_quarter(self):
        self.assertEqual(nd.classify(samples([2]),"第二节")['name'],'Q2.mp4')
        self.assertEqual(nd.classify(samples([2]),"全场录像 第2节")['name'],'Q2.mp4')

    def test_full_game_and_overtime(self):
        self.assertEqual(nd.classify(samples([1,2,3,4]),"")['name'],'FULL.mp4')
        self.assertEqual(nd.classify(samples([1,2,3,4,5]),"")['periods'],[1,2,3,4,5])
        self.assertEqual(nd.classify(samples([6]),"第二个加时")['name'],'OT2.mp4')

    def test_partial_multi_period_and_noise(self):
        data=samples([2,3]);data.append(dict(t=900,period=4,clock=60))
        self.assertEqual(nd.classify(data,"")['name'],'Q2-Q3.mp4')

    def test_title_conflict_and_highlights(self):
        for title in ['第3节','全场录像','湖人得分集锦']:
            with self.assertRaises(ValueError):nd.classify(samples([2]),title)

    def test_sparse_frozen_and_reversed_periods(self):
        for data in [[dict(t=0,period=2,clock=600)],
                     [dict(t=i*20,period=2,clock=600) for i in range(10)],samples([3,2])]:
            with self.assertRaises(ValueError):nd.classify(data,"")

    def test_url_boundary(self):
        self.assertEqual(nd.validate_url('https://weibo.com/123/abc'),'https://weibo.com/123/abc')
        for url in ['file:///tmp/a','https://weibo.com.evil.test/x','https://evil.test/x','https://user:password@weibo.com/x']:
            with self.assertRaises(ValueError):nd.validate_url(url)

    def test_generic_overtime_uses_official_scores(self):
        pbp=dict(actions=[dict(period=5,scoreHome='100',scoreAway='100'),dict(period=5,scoreHome='110',scoreAway='110'),
                          dict(period=6,scoreHome='110',scoreAway='110'),dict(period=6,scoreHome='120',scoreAway='118')])
        data=[dict(t=i*20,period=5,clock=280-i*20,home=114,away=112,raw=dict(period='OT')) for i in range(5)]
        self.assertEqual(nh.resolve_overtime(data,pbp)[0]['period'],6)
        ambiguous=[dict(t=0,period=5,clock=300,home=110,away=110,raw=dict(period='OT'))]
        self.assertIsNone(nh.resolve_overtime(ambiguous,pbp)[0]['period'])

    def test_unresolved_overtime_not_silently_omitted_from_full_game(self):
        data=samples([1,2,3,4])+[dict(t=500+i*20,period=None,clock=250-i*20,raw=dict(period='OT')) for i in range(4)]
        with self.assertRaisesRegex(ValueError,'加时编号'):nd.classify(data,'')

    def test_single_official_overtime_without_scores(self):
        pbp=dict(actions=[dict(period=5,scoreHome='110',scoreAway='110')])
        data=[dict(t=0,period=5,clock=280,home=None,away=None,raw=dict(period='OT'))]
        self.assertEqual(nh.resolve_overtime(data,pbp)[0]['period'],5)

    def test_overtime_label_parser(self):
        for label,expected in [('OT',5),('2OT',6),('OT3',7)]:
            raw=dict(clock=[dict(text='4:03')],period=[dict(text=label)])
            self.assertEqual(nh.parse_ocr(raw,0)['period'],expected)


class ProgressTests(unittest.TestCase):
    def test_progress_inherits_terminal_without_forcing_newlines(self):
        with patch.object(nd.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(nd.invoke_ytdlp(['https://weibo.com/123/a'],progress=True),'')
        self.assertNotIn('--newline',run.call_args.args[0])
        self.assertNotIn('--no-color',run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs,{})

    def test_native_progress_failure_still_raises(self):
        with patch.object(nd.subprocess,'run',return_value=SimpleNamespace(returncode=1)),self.assertRaisesRegex(RuntimeError,'状态码 1'):
            nd.invoke_ytdlp(['https://weibo.com/123/a'],progress=True)


class ArchiveTests(unittest.TestCase):
    def setup_download(self,root):
        root_patch=patch.object(nh,'ROOT',root)
        root_patch.start();self.addCleanup(root_patch.stop)
        url='https://weibo.com/123/test'
        stage=root/'.downloads'/hashlib.sha256(url.encode()).hexdigest()[:16]
        stage.mkdir(parents=True)
        nh.atomic_json(stage/'metadata.json',dict(id='12345',title='第2节',description=''))
        args=SimpleNamespace(urls=[url],game_id='0012600036',game_dir=str(root),refresh_data=False)
        pbp=dict(actions=[dict(period=2)])
        return args,stage,pbp,dict(home='LAL',away='SAC')

    def test_download_error_records_and_preserves_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);args,stage,pbp,info=self.setup_download(root)
            (stage/'video.mp4.part').write_bytes(b'partial')
            with patch.object(nd,'context',return_value=(root,pbp,info)),patch.object(nd,'download_media',side_effect=RuntimeError('offline')),self.assertRaises(RuntimeError):
                nd.download_all(args)
            self.assertEqual((stage/'video.mp4.part').read_bytes(),b'partial')
            self.assertIn('offline',json.loads((stage/'error.json').read_text())['error'])

    def test_completed_batch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);args,stage,pbp,info=self.setup_download(root)
            source=stage/'video.mp4';source.write_bytes(b'video')
            result=dict(name='Q2.mp4',periods=[2],full=False,evidence={})
            with patch.object(nd,'context',return_value=(root,pbp,info)),patch.object(nd,'download_media',return_value=source) as download,patch.object(nd,'identify_video',return_value=result),patch.object(nh,'probe',return_value=100):
                nd.download_all(args);nd.download_all(args)
            download.assert_called_once()
            self.assertEqual((root/'Q2.mp4').read_bytes(),b'video')
            self.assertFalse(source.exists())
            self.assertEqual(len(json.loads((root/'recordings.json').read_text())['recordings']),1)
            self.assertEqual(json.loads((root/'output'/'0012600036'/'download.json').read_text())['game_dir'],str(root))

    def test_full_game_missing_official_overtime_is_not_archived(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);args,stage,pbp,info=self.setup_download(root)
            pbp['actions']=[dict(period=p) for p in range(1,6)]
            source=stage/'video.mp4';source.write_bytes(b'video')
            result=dict(name='FULL.mp4',periods=[1,2,3,4],full=True,evidence={})
            with patch.object(nd,'context',return_value=(root,pbp,info)),patch.object(nd,'download_media',return_value=source),patch.object(nd,'identify_video',return_value=result),self.assertRaisesRegex(RuntimeError,'全部加时'):
                nd.download_all(args)
            self.assertTrue(source.exists())
            self.assertFalse((root/'FULL.mp4').exists())

    def test_archive_keeps_download_until_manifest_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'incoming.mp4';source.write_bytes(b'video');target=root/'Q2.mp4'
            nd.archive(source,target)
            self.assertTrue(source.samefile(target))
            nd.archive(source,target)
            self.assertEqual(target.read_bytes(),b'video')

    def test_collision_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'Q2.mp4';target.write_bytes(b'old');source=root/'incoming.mp4';source.write_bytes(b'new')
            with self.assertRaises(FileExistsError):nd.archive(source,target)
            self.assertEqual(target.read_bytes(),b'old');self.assertEqual(source.read_bytes(),b'new')

    def test_archive_and_discover_full_with_existing_quarter(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Q1.mp4').write_bytes(b'q1');(root/'FULL.mp4').write_bytes(b'full');(root/'OT2.mp4').write_bytes(b'ot')
            nh.atomic_json(root/'recordings.json',dict(game_id='0012600036',recordings=[dict(file='FULL.mp4',periods=[1,2,3,4])]))
            videos=nh.discover_videos(root,'0012600036')
            self.assertEqual(videos[1].name,'Q1.mp4');self.assertEqual(videos[2].name,'FULL.mp4');self.assertEqual(videos[6].name,'OT2.mp4')
            with self.assertRaises(ValueError):nh.discover_videos(root,'0012600028')

    def test_download_resume_and_failure_keeps_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            stage=Path(folder);(stage/'video.mp4').write_bytes(b'complete')
            with patch.object(nd,'invoke_ytdlp') as invoke,patch.object(nh,'probe',return_value=120):
                self.assertEqual(nd.download_media('https://weibo.com/123/a',stage).name,'video.mp4')
                invoke.assert_not_called()
            (stage/'video.mp4').unlink();(stage/'video.mp4.part').write_bytes(b'partial')
            with patch.object(nd,'invoke_ytdlp',side_effect=RuntimeError('offline')),self.assertRaises(RuntimeError):
                nd.download_media('https://weibo.com/123/a',stage)
            self.assertEqual((stage/'video.mp4.part').read_bytes(),b'partial')

    def test_analysis_scans_full_recording_once_and_handles_overtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'FULL.mp4';video.write_bytes(b'fixture')
            nh.atomic_json(root/'recordings.json',dict(game_id='0012600036',recordings=[dict(file='FULL.mp4',periods=[1,2,3,4,5])]))
            events=[dict(event_id=str(p),player='doncic',kind='score',period=p,clock='PT04M00S',description='test') for p in [2,5]]
            pbp=dict(actions=events)
            info=dict(home='LAL',away='SAC')
            with patch.object(nh,'ROOT',root),patch.object(nh,'load_game',return_value=(pbp,info)),patch.object(nh,'select_events',return_value=events),patch.object(nh,'vision_binary'),patch.object(nh,'probe',return_value=1000),patch.object(nh,'scan_video',return_value=[]) as scan,patch.object(nh,'locate_event',return_value=dict(candidates=[100],status='keep',reason='test')),patch.object(nh,'render_clip') as render:
                nh.analyze(SimpleNamespace(game_id='0012600036',game_dir=str(root),refresh_data=False,players=['doncic']))
            scan.assert_called_once()
            self.assertEqual(render.call_args_list[-1].args[-1].name,'doncic_OT1_5.mp4')
            self.assertFalse(json.loads((root/'output'/'0012600036'/'analysis.json').read_text())['partial'])


if __name__=='__main__':unittest.main()
