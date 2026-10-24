import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nba_highlight_maker import download as nd
from nba_highlight_maker import processing as nh
from tests.test_download import samples
from tests.test_nba_highlights import shot


class SourceTests(unittest.TestCase):
    def test_youtube_urls_and_platform_identity(self):
        for url in ['https://www.youtube.com/watch?v=abc', 'https://youtu.be/abc']:
            self.assertEqual(nd.validate_url(url), url)
            self.assertEqual(nd.platform(url), 'youtube')
        for url in ['https://youtube.com.evil.test/a', 'https://user@youtu.be/a']:
            with self.assertRaises(ValueError): nd.validate_url(url)

    def test_highlights_allow_sparse_and_reversed_periods_without_claiming_full(self):
        data=[dict(t=i,period=p,clock=200) for i,p in enumerate([3,1,4])]
        result=nd.classify(data,'Full Game Highlights',source_type='highlights')
        self.assertEqual(result['periods'],[1,3,4])
        self.assertFalse(result['full'])
        with self.assertRaises(ValueError): nd.classify([], '', source_type='highlights')
        with self.assertRaises(ValueError): nd.classify(samples([1]), 'Highlights')

    def test_all_sources_are_kept_for_each_period(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ['Q1.mp4','HL-a.mp4','HL-b.mp4']: (root/name).write_bytes(b'x')
            nh.atomic_json(root/'recordings.json',dict(game_id='0012600036',recordings=[
                dict(file=name,periods=[1,2],source_type='highlights',ocr_profile='spectrum') for name in ['HL-a.mp4','HL-b.mp4']]))
            sources=nh.discover_sources(root,'0012600036')
            self.assertEqual(len(sources[1]),3)
            self.assertEqual(len(sources[2]),2)
            self.assertEqual(sources[2][0]['ocr_profile'],'spectrum')

    def test_multiple_source_candidates_require_review(self):
        with patch.object(nh,'locate_event',return_value=dict(candidates=[10],status='keep',reason='candidate')):
            result=nh.locate_sources(shot(),[Path('/tmp/a.mp4'),Path('/tmp/b.mp4')],{Path('/tmp/a.mp4'):[],Path('/tmp/b.mp4'):[]})
        self.assertEqual(result['status'],'pending')
        self.assertEqual(len(result['source_candidates']),2)
        with patch.object(nh,'locate_event',return_value=dict(candidates=[],status='pending',reason='no evidence')):
            result=nh.locate_sources(shot(),[Path('/tmp/a.mp4')],{Path('/tmp/a.mp4'):[]})
        self.assertEqual(result['status'],'pending')
        self.assertEqual(result['source_candidates'],[])

    def test_missing_requires_reason_and_is_not_pending(self):
        nh.validate_review([dict(status='missing',reason='人工检查全部源视频，未收录')])
        with self.assertRaises(ValueError): nh.validate_review([dict(status='missing',reason='')])
        with self.assertRaises(ValueError): nh.validate_review([dict(status='pending',reason='OCR failed')])

    def test_mark_missing_does_not_render_or_overwrite_other_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            review=Path(folder)/'review.csv'
            row=dict.fromkeys(nh.FIELDS,'');row.update(event_id='87',player='doncic',period='1',status='pending')
            with review.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
            args=SimpleNamespace(review=str(review),event_id='87',player=None,status='missing',reason='人工检查未收录')
            with patch.object(nh,'render_clip') as render: nh.mark(args)
            render.assert_not_called()
            with review.open(encoding='utf-8-sig') as f: updated=next(csv.DictReader(f))
            self.assertEqual(updated['status'],'missing')
            self.assertEqual(updated['reason'],args.reason)

    def test_highlight_analysis_keeps_unobserved_periods_pending_and_scans_each_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'analysis';output.mkdir()
            paths=[root/'HL-a.mp4',root/'HL-b.mp4']
            for path in paths: path.write_bytes(b'fixture')
            nh.atomic_json(root/'recordings.json',dict(game_id='0012600036',recordings=[
                dict(file=p.name,periods=[1],source_type='highlights',ocr_profile='spectrum') for p in paths]))
            events=nh.select_events(dict(actions=[shot(),dict(shot(2),period=2)]),{1,2})
            with patch.object(nh,'load_game',return_value=(dict(actions=[shot(),dict(shot(2),period=2)]),dict(home='LAL',away='SAC'))),patch.object(nh,'vision_binary'),patch.object(nh,'probe',return_value=100),patch.object(nh,'scan_video',return_value=[]) as scan,patch.object(nh,'locate_event',return_value=dict(candidates=[10],status='keep',reason='test')),patch.object(nh,'render_clip'):
                args=SimpleNamespace(game_id='0012600036',game_dir=str(root),refresh_data=False,output_dir=str(output))
                nh.analyze(args)
                nh.mark(SimpleNamespace(review=str(output/'review.csv'),event_id='1',player='doncic',status='missing',reason='人工确认'))
                nh.analyze(args)
                with (output/'review.csv').open(encoding='utf-8-sig') as f:
                    unchanged=[r for r in csv.DictReader(f) if r['event_id']=='1' and r['player']=='doncic'][0]
                self.assertEqual(unchanged['status'],'missing')
                paths[0].write_bytes(b'changed source')
                nh.analyze(args)
            self.assertEqual(scan.call_count,6)
            self.assertTrue(all(c.kwargs['ocr_profile']=='spectrum' for c in scan.call_args_list))
            with (output/'review.csv').open(encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),len(events))
            self.assertTrue(all(r['status']=='pending' for r in rows))
            unobserved=[r for r in rows if r['period']=='2']
            self.assertTrue(all(r['source']=='' for r in unobserved))
            self.assertTrue(json.loads((output/'analysis.json').read_text())['partial'])

    def test_adjust_can_choose_another_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);review=root/'review.csv'
            row=dict.fromkeys(nh.FIELDS,'');row.update(event_id='1',player='doncic',period='1',status='pending',source='/old.mp4')
            with review.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
            source=root/'new.mp4'
            with patch.object(nh,'probe',return_value=100),patch.object(nh,'render_clip') as render:
                nh.adjust(SimpleNamespace(review=str(review),event_id='1',player=None,start='00:05',end='00:15',source=str(source)))
            self.assertEqual(render.call_args.args[0],source.resolve())
            with review.open(encoding='utf-8-sig') as f: updated=next(csv.DictReader(f))
            self.assertEqual(updated['source'],str(source.resolve()))
            self.assertEqual(updated['status'],'keep')

    def test_highlights_and_confirmed_missing_do_not_add_title_markers(self):
        from nba_highlight_maker import cover as cover
        from tests.test_cover import INFO
        row=dict(player='doncic',period='1',event_id='1',kind='assist',status='missing')
        for rows,partial_source in [([row],False),([],True)]:
            title,description=cover.publication_text(INFO,rows,[1,2,3,4],partial_source=partial_source)
            self.assertNotIn('部分',title)
            self.assertEqual(description,'2026–27 NBA 季前赛，国王客场对阵湖人。\n比赛日期：2026.10.09')

    def test_highlight_download_records_profile_and_preserves_two_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            urls=['https://youtu.be/a','https://youtu.be/b']
            args=SimpleNamespace(urls=urls,game_id='0012600036',game_dir=str(root),refresh_data=False,source_type='highlights',ocr_profile='spectrum')
            def download(url,stage):
                path=stage/'video.mp4';path.write_bytes(url.encode());return path
            def metadata(arguments): return json.dumps(dict(id=arguments[-1].split('/')[-1],title='Highlights'))
            with patch.object(nd,'context',return_value=(root,dict(actions=[dict(period=1)]),dict(home='LAL',away='SAC'))),patch.object(nd,'invoke_ytdlp',side_effect=metadata),patch.object(nd,'download_media',side_effect=download),patch.object(nd,'identify_video',return_value=dict(name='HIGHLIGHTS.mp4',periods=[1],full=False,evidence={})):
                nd.download_all(args)
            records=json.loads((root/'recordings.json').read_text())['recordings']
            self.assertEqual(len({r['file'] for r in records}),2)
            self.assertTrue(all(r['platform']=='youtube' and r['ocr_profile']=='spectrum' for r in records))
            self.assertTrue(all((root/r['file']).is_file() for r in records))

    def test_export_separates_missing_from_excluded_and_does_not_probe_missing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);review=root/'review.csv'
            rows=[]
            for event,status in [('1','keep'),('2','missing'),('3','exclude')]:
                row=dict.fromkeys(nh.FIELDS,'')
                row.update(event_id=event,player='doncic',period='1',kind='score',source=str(root/'source.mp4') if status=='keep' else '',start='0',end='10',status=status,reason='人工确认')
                rows.append(row)
            with review.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=nh.FIELDS);writer.writeheader();writer.writerows(rows)
            def concatenate(command,progress=None): Path(command[-1]).write_bytes(b'final');return ''
            with patch.object(nh,'ROOT',root),patch('nba_highlight_maker.cover.generate_cover',return_value=root/'cover.png'),patch.object(nh,'probe',return_value=100) as probe,patch.object(nh,'render_clip'),patch.object(nh,'run',side_effect=concatenate):
                nh.export(SimpleNamespace(review=str(review)))
            self.assertNotIn(root.resolve(),[call.args[0] for call in probe.call_args_list])
            metadata=json.loads((root/'export.json').read_text())
            self.assertEqual(metadata['players']['doncic'],dict(kept=1,missing=1,excluded=1))
            selection=json.loads((root/'selection.json').read_text())
            self.assertEqual(selection['missing'][0]['event_id'],'2')

    def test_same_period_and_cross_platform_id_are_archived_as_distinct_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            args=SimpleNamespace(urls=['https://weibo.com/123/a','https://youtu.be/a'],game_id='0012600036',game_dir=str(root),refresh_data=False,source_type='recording',ocr_profile='espn')
            def download(url,stage):
                path=stage/'video.mp4';path.write_bytes(url.encode());return path
            with patch.object(nd,'context',return_value=(root,dict(actions=[dict(period=1)]),dict(home='LAL',away='SAC'))),patch.object(nd,'invoke_ytdlp',return_value=json.dumps(dict(id='same-id',title='Q1'))),patch.object(nd,'download_media',side_effect=download),patch.object(nd,'identify_video',side_effect=lambda *args:dict(name='Q1.mp4',periods=[1],full=False,evidence={})):
                nd.download_all(args)
            records=json.loads((root/'recordings.json').read_text())['recordings']
            self.assertEqual(len(records),2)
            self.assertEqual({r['platform'] for r in records},{'weibo','youtube'})
            self.assertEqual((root/'Q1.mp4').read_bytes(),args.urls[0].encode())
            self.assertEqual(len(nh.discover_sources(root,args.game_id)[1]),2)


if __name__=='__main__': unittest.main()
