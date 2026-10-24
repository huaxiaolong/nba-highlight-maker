import csv
import tempfile
import unittest
import http.client
import json
import threading
from pathlib import Path
from unittest.mock import patch

from nba_highlight_maker import processing as nh
from nba_highlight_maker import review as review


class ReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.output=self.root/'output'/'0012600036';self.output.mkdir(parents=True)
        self.source=self.root/'source.mp4';self.source.write_bytes(b'0123456789')
        self.csv=self.output/'review.csv'
        self.rows=[]
        for player in ['doncic','reaves']:
            row=dict.fromkeys(nh.FIELDS,'')
            row.update(event_id='5',player=player,period='1',kind='score',clock='PT10M00S',description='回合',source=str(self.source),start='00:01',end='00:04',status='pending',reason='OCR 候选')
            self.rows.append(row)
        self.write()
        self.store=review.ReviewStore(self.root)
        self.identifier=self.store.library()[0]['id']

    def write(self):
        with self.csv.open('w',encoding='utf-8-sig',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerows(self.rows)

    def decision(self,**changes):
        data=dict(id=self.identifier,revision=self.store.snapshot(self.identifier)['revision'],index=0,status='missing',reason='逐段检查全部源视频，未收录')
        data.update(changes)
        return self.store.decide(data)

    def test_opening_catalog_and_order_are_local_validated_and_persistent(self):
        media=self.root/'media';media.mkdir()
        for name in ['second.MOV','first.mp4','notes.txt']:(media/name).write_bytes(b'fixture')
        (media/'outside.mp4').symlink_to(self.source)
        snapshot=self.store.snapshot(self.identifier)
        files=snapshot['opening_files']
        self.assertEqual([p['name'] for p in files],['first.mp4','second.MOV'])
        self.assertEqual(snapshot['prepend'],[])
        (self.output/'cover.png').write_bytes(b'cover')
        (self.output/'title.txt').write_text('title')
        (self.output/'highlights.mp4').write_bytes(b'video')
        (self.output/'export.json').write_text(json.dumps(dict(review_sha256=snapshot['revision'],prepend=[])))
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['state'],'ready')
        paths=[p['path'] for p in reversed(files)]
        request=dict(id=self.identifier,revision=snapshot['revision'],prepend=paths)
        before=self.csv.read_bytes()
        result=self.store.select_opening(request)
        self.assertEqual(result['prepend'],paths)
        self.assertEqual(result['publication']['state'],'stale')
        self.assertEqual(self.csv.read_bytes(),before)
        self.assertEqual(self.store.snapshot(self.identifier)['prepend'],paths)
        for invalid in [[str(self.source)], [paths[0],paths[0]], 'first.mp4', [True]]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                self.store.select_opening(dict(request,prepend=invalid))
        self.assertEqual(self.store.snapshot(self.identifier)['prepend'],paths)
        with self.assertRaises(review.ConflictError):self.store.select_opening(dict(request,revision='stale'))
        Path(paths[0]).unlink()
        snapshot=self.store.snapshot(self.identifier)
        self.assertEqual(snapshot['prepend'],paths)
        self.assertNotIn(paths[0],[p['path'] for p in snapshot['opening_files']])
        self.store.select_opening(dict(request,prepend=[]))
        self.assertEqual(self.store.snapshot(self.identifier)['prepend'],[])

    def test_nested_library_and_old_csv_are_supported(self):
        nested=self.output/'youtube-abc';nested.mkdir()
        (nested/'review.csv').write_bytes(self.csv.read_bytes())
        self.assertEqual(len(self.store.library()),2)
        data=self.store.snapshot(self.identifier)
        self.assertEqual(data['counts']['pending'],2)
        self.assertTrue(data['rows'][0]['source_url'].startswith('/media/'))
        self.assertNotIn(str(self.root),data['rows'][0]['source_url'])

    def test_library_labels_use_beijing_date_teams_and_distinct_versions(self):
        from tests.test_cover import INFO
        (self.output/'source.json').write_text(json.dumps(INFO))
        for name in ['youtube-abc','youtube-def']:
            folder=self.output/name;folder.mkdir()
            (folder/'review.csv').write_bytes(self.csv.read_bytes())
        boards=self.store.library()
        self.assertEqual([b['label'] for b in boards],[
            '2026.10.09 国王 vs 湖人 · 默认版本',
            '2026.10.09 国王 vs 湖人 · YouTube 集锦 1',
            '2026.10.09 国王 vs 湖人 · YouTube 集锦 2'])
        self.assertEqual(boards[0]['id'],self.identifier)
        for content in ['{broken','{"game_id":"0012600036","game":[]}']:
            (self.output/'source.json').write_text(content)
            self.assertEqual(self.store.library()[0]['label'],'0012600036')

    def test_mark_is_durable_and_preserves_shared_event_other_player(self):
        result=self.decision()
        self.assertEqual(result['rows'][0]['status'],'missing')
        self.assertEqual(result['rows'][1]['status'],'pending')
        with self.csv.open(encoding='utf-8-sig') as file: rows=list(csv.DictReader(file))
        self.assertEqual(rows[0]['reason'],'逐段检查全部源视频，未收录')

    def test_select_players_adds_pending_events_without_changing_existing_review(self):
        from tests.test_players import game_info
        info=game_info()
        info['game']['homeTeam']['players'].append(dict(personId=1630559,firstName='Austin',familyName='Reaves',statistics=dict(minutes='20:00')))
        (self.output/'source.json').write_text(json.dumps(info))
        action=dict(actionId=7,period=1,clock='PT10M00S',personId=100,assistPersonId=1629029,
                    isFieldGoal=1,shotResult='Made',description='Player dunk',scoreHome='5',scoreAway='2')
        (self.output/'play_by_play.json').write_text(json.dumps(dict(actions=[action])))
        old=self.csv.read_bytes()
        initial=self.store.snapshot(self.identifier)
        self.assertEqual({p['key'] for p in initial['roster']},{'doncic','reaves','100','201939'})
        for players in [[],['101'],['unknown']]:
            with self.assertRaises(ValueError):self.store.select_players(dict(id=self.identifier,revision=initial['revision'],players=players))
            self.assertEqual(self.csv.read_bytes(),old)
        result=self.store.select_players(dict(id=self.identifier,revision=initial['revision'],players=['100','doncic']))
        self.assertEqual(result['selected_players'],['100','doncic'])
        self.assertEqual(self.store.snapshot(self.identifier)['selected_players'],['100','doncic'])
        self.assertEqual(len(result['rows']),4)
        self.assertEqual([(r['status'],r['reason']) for r in result['rows'][:2]],[(r['status'],r['reason']) for r in self.rows])
        self.assertTrue(all(r['status']=='pending' for r in result['rows'][2:]))
        with self.assertRaises(review.ConflictError):
            self.store.select_players(dict(id=self.identifier,revision=initial['revision'],players=['201939']))
        again=self.store.select_players(dict(id=self.identifier,revision=result['revision'],players=['100','doncic']))
        self.assertEqual(len(again['rows']),4)

    def test_stale_revision_cannot_overwrite_external_edit(self):
        revision=self.store.snapshot(self.identifier)['revision']
        self.rows[0]['reason']='外部修改';self.write();before=self.csv.read_bytes()
        with self.assertRaises(review.ConflictError): self.decision(revision=revision)
        self.assertEqual(self.csv.read_bytes(),before)

    def test_keep_reuses_adjust_and_validates_source(self):
        with patch.object(nh,'probe',return_value=10),patch.object(nh,'render_clip') as render:
            result=self.decision(status='keep',source=str(self.source),start='00:02',end='00:05',reason='已确认动作完整')
        self.assertEqual(result['rows'][0]['status'],'keep')
        self.assertEqual(result['rows'][0]['reason'],'已确认动作完整')
        self.assertEqual(render.call_args.args[1:3],(2,5))
        with self.assertRaises(ValueError): self.decision(status='keep',source='/etc/passwd',start='0',end='1')

    def test_bad_range_and_encoding_failure_do_not_change_csv(self):
        before=self.csv.read_bytes()
        with patch.object(nh,'probe',return_value=10),self.assertRaises(ValueError):
            self.decision(status='keep',source=str(self.source),start='5',end='2')
        self.assertEqual(self.csv.read_bytes(),before)
        with patch.object(nh,'probe',return_value=10),patch.object(nh,'render_clip',side_effect=RuntimeError('编码失败')),self.assertRaises(RuntimeError):
            self.decision(status='keep',source=str(self.source),start='1',end='4')
        self.assertEqual(self.csv.read_bytes(),before)

    def test_invalid_status_reason_index_and_board_are_rejected(self):
        for changes in [dict(status='missing',reason=' '),dict(status='unknown'),dict(index=-1),dict(index=True),dict(id='../../etc/passwd')]:
            with self.subTest(changes=changes),self.assertRaises((ValueError,KeyError)): self.decision(**changes)

    def test_external_edit_during_preview_render_is_not_overwritten(self):
        def external_edit(*args,**kwargs):
            self.rows[0]['reason']='编码期间的外部修改';self.write()
        with patch.object(nh,'probe',return_value=10),patch.object(nh,'render_clip',side_effect=external_edit),self.assertRaises(review.ConflictError):
            self.decision(status='keep',source=str(self.source),start='1',end='4')
        with self.csv.open(encoding='utf-8-sig') as file: rows=list(csv.DictReader(file))
        self.assertEqual(rows[0]['reason'],'编码期间的外部修改')

    def test_publication_reads_only_current_board_and_detects_stale_export(self):
        (self.output/'cover.png').write_bytes(b'png')
        (self.output/'title.txt').write_text('湖人比赛标题\n')
        (self.output/'title_douyin.txt').write_text('湖人得分助攻集锦\n')
        (self.output/'description.txt').write_text('比赛简介\n')
        (self.output/'highlights.mp4').write_bytes(b'final')
        revision=self.store.snapshot(self.identifier)['revision']
        (self.output/'export.json').write_text(json.dumps(dict(review_sha256=revision)))
        publication=self.store.snapshot(self.identifier)['publication']
        self.assertEqual(publication['state'],'ready')
        self.assertEqual(publication['export_options'],dict(prepend=[],watermark=True))
        (self.output/'export.json').write_text(json.dumps(dict(review_sha256=revision,scenery_enabled=False,watermark=dict(enabled=False))))
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['export_options'],dict(prepend=[],watermark=False))
        (self.output/'export.json').write_text(json.dumps(dict(review_sha256=revision,prepend=['/tmp/custom.mov'],watermark=dict(enabled=False))))
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['export_options'],dict(prepend=['/tmp/custom.mov'],watermark=False))
        self.assertEqual(publication['title'],'湖人比赛标题')
        self.assertEqual(publication['douyin_title'],'湖人得分助攻集锦')
        self.assertEqual(self.store.publication_file(self.identifier,'title_douyin.txt'),(self.output/'title_douyin.txt').resolve())
        self.assertEqual(publication['description'],'比赛简介')
        self.assertEqual(publication['covers'][0]['ratio'],'16:9')
        self.assertTrue(publication['covers'][0]['url'].endswith('/cover.png'))
        self.assertTrue(publication['video']['url'].endswith('/highlights.mp4'))
        self.decision()
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['state'],'stale')
        nested=self.output/'youtube-abc';nested.mkdir();(nested/'review.csv').write_bytes(self.csv.read_bytes())
        other=next(item['id'] for item in self.store.library() if item['label'].endswith('youtube-abc'))
        self.assertEqual(self.store.snapshot(other)['publication']['state'],'missing')
        self.assertEqual(self.store.snapshot(other)['publication']['douyin_title'],'')

    def test_missing_incomplete_and_unverified_publication_do_not_break_review(self):
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['state'],'missing')
        (self.output/'title.txt').write_text('只有标题')
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['state'],'incomplete')
        (self.output/'cover.png').write_bytes(b'png');(self.output/'highlights.mp4').write_bytes(b'video')
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['state'],'unverified')
        (self.output/'export.json').write_text('{broken')
        self.assertEqual(self.store.snapshot(self.identifier)['publication']['state'],'unverified')
        with self.assertRaises(ValueError):self.store.publication_file(self.identifier,'../source.mp4')
        (self.output/'cover_4x3.png').symlink_to(self.source)
        with self.assertRaises(ValueError):self.store.publication_file(self.identifier,'cover_4x3.png')

    def test_http_range_and_request_boundaries(self):
        server=review.ThreadingHTTPServer(('127.0.0.1',0),review.ReviewHandler)
        server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,headers=None,body=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read())
            connection.close();return result
        data=self.store.snapshot(self.identifier)
        status,headers,body=request('GET','/api/review.csv?id='+self.identifier)
        self.assertEqual((status,body),(200,self.csv.read_bytes()))
        self.assertEqual(request('POST','/api/players',{'Content-Type':'application/json'},
                                 json.dumps(dict(id=self.identifier,revision=data['revision'],players=[])))[0],400)
        opening=dict(id=self.identifier,revision=data['revision'],prepend=[])
        self.assertEqual(request('POST','/api/opening',{'Content-Type':'application/json'},json.dumps(opening))[0],200)
        self.assertEqual(request('POST','/api/opening',{'Content-Type':'application/json'},
                                 json.dumps(dict(opening,prepend=[str(self.source)])))[0],400)
        self.assertEqual(request('POST','/api/opening',{'Content-Type':'application/json','Origin':'https://evil.example'},json.dumps(opening))[0],403)
        (self.output/'cover.png').write_bytes(b'png')
        status,headers,body=request('GET','/files/'+self.identifier+'/cover.png')
        self.assertEqual((status,body),(200,b'png'))
        self.assertEqual(headers['Content-Type'],'image/png')
        self.assertEqual(request('GET','/files/'+self.identifier+'/../source.mp4')[0],404)
        media=data['rows'][0]['source_url']
        status,headers,body=request('GET',media,{'Range':'bytes=2-4'})
        self.assertEqual((status,body),(206,b'234'))
        self.assertEqual(headers['Content-Range'],'bytes 2-4/10')
        self.assertEqual(request('GET',media,{'Range':'bytes=10-'})[0],416)
        self.assertEqual(request('GET','/media/../../etc/passwd')[0],404)
        self.assertEqual(request('GET','/api/reviews',{'Host':'evil.example'})[0],403)
        self.assertEqual(request('GET','/api/reviews',{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(request('POST','/api/decision',{'Content-Type':'text/plain'},'{}')[0],415)
        payload=dict(id=self.identifier,revision='stale',index=0,status='missing',reason='人工确认')
        self.assertEqual(request('POST','/api/decision',{'Content-Type':'application/json'},json.dumps(payload))[0],409)


class RangeTests(unittest.TestCase):
    def test_normal_open_and_suffix_ranges(self):
        for header,expected in [('bytes=2-4',(2,4)),('bytes=2-',(2,9)),('bytes=-3',(7,9)),('bytes=0-99',(0,9))]:
            self.assertEqual(review.byte_range(header,10),expected)
        for header in ['bytes=10-','bytes=4-2','bytes=-0','bytes=0-1,3-4','bytes=-','bad']:
            with self.assertRaises(ValueError): review.byte_range(header,10)
        with self.assertRaises(ValueError):review.byte_range('bytes=0-',0)


if __name__=='__main__':unittest.main()
