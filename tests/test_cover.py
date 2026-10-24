import copy
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from nba_highlight_maker import cover as cover

INFO={'game_id':'0012600036','game':{'gameId':'0012600036','gameCode':'20261008/SACLAL','gameTimeUTC':'2026-10-09T02:30:00Z','gameLabel':'Preseason','awayTeam':{'teamId':1610612758,'teamTricode':'SAC','teamName':'Kings'},'homeTeam':{'teamId':1610612747,'teamTricode':'LAL','teamName':'Lakers'}}}
class CoverTests(unittest.TestCase):
    def test_regular_season_without_game_label_uses_game_id(self):
        for label in (None,''):
            info=copy.deepcopy(INFO)
            info['game_id']=info['game']['gameId']='0022600005'
            info['game']['gameTimeUTC']='2026-10-22T02:00:00Z'
            info['game']['gameLabel']=label
            self.assertEqual(cover.cover_info(info)['stage'],'常规赛')

    def test_kessler_publication_title(self):
        row=dict(player='kessler',period='1',event_id='5',kind='score',status='keep')
        title,_=cover.publication_text(INFO,[row],[1])
        self.assertIn('Walker Kessler得分助攻集锦',title)

    def test_three_core_titles_keep_content_without_season_or_partial_marker(self):
        info=copy.deepcopy(INFO)
        info['game']['awayTeam']['teamTricode']='GSW'
        rows=[dict(player=p,period='1',event_id=str(i),kind='score',status='keep')
              for i,p in enumerate(('doncic','reaves','kessler'))]
        rows.append(dict(player='reaves',period='4',event_id='439',kind='assist',status='missing'))
        title,_=cover.publication_text(info,rows,[1],partial_source=True)
        short,_=cover.publication_text(info,rows,[1],douyin=True,partial_source=True)
        self.assertEqual(title,'勇士 VS 湖人｜湖人三核得分助攻集锦｜季前赛 2026.10.09')
        self.assertEqual(short,'2026.10.09 勇士vs湖人 湖人三核得分助攻集锦')
        self.assertLessEqual(len(short),30)

    def test_douyin_titles_fit_30_characters_for_all_team_pairs(self):
        for away in cover.NAMES:
            for home in cover.NAMES:
                info=copy.deepcopy(INFO)
                info['game']['awayTeam']['teamTricode']=away
                info['game']['homeTeam']['teamTricode']=home
                info['game']['period']=4
                rows=[dict(player=p,period='1',event_id='5',kind='score',status='keep')
                      for p in ('doncic','reaves','kessler')]
                title,_=cover.publication_text(info,rows,[1],douyin=True)
                self.assertLessEqual(len(title),30)
                self.assertIn('2026.10.09',title)
                self.assertIn('湖人三核得分助攻集锦',title)
                self.assertNotIn('部分',title)
                self.assertTrue(cover.NAMES[away]+'vs'+cover.NAMES[home] in title or away+'vs'+home in title)
    def test_portrait_render_failure_preserves_all_previous_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory);(output/'source.json').write_text(json.dumps(INFO))
            files=['cover.png','cover_4x3.png','cover_3x4.png','title.txt','title_douyin.txt','description.txt']
            for name in files:(output/name).write_bytes(b'previous output')
            def render(command):
                if command[0]=='swiftc':return ''
                data=json.loads(Path(command[1]).read_text())
                width=data['width']
                if width==1080:
                    self.assertEqual(data['height'],1440)
                    raise RuntimeError('3:4 render failed')
                self.assertIn(width,[1920,1440])
                Path(command[-1]).write_bytes(b'new cover')
            with patch.object(cover,'cache_logos',return_value=output),patch.object(cover.nh,'run',side_effect=render):
                with self.assertRaisesRegex(RuntimeError,'3:4 render failed'):cover.generate_cover(output)
            for name in files:self.assertEqual((output/name).read_bytes(),b'previous output')
    def test_publication_title_uses_kept_players_and_description_has_only_two_lines(self):
        info=copy.deepcopy(INFO);info['game']['period']=4
        rows=[dict(player='doncic',period='1',event_id='5',kind='score',status='keep'),
              dict(player='reaves',period='1',event_id='5',kind='assist',status='keep'),
              dict(player='reaves',period='1',event_id='7',kind='score+assist',status='keep'),
              dict(player='doncic',period='1',event_id='8',kind='score',status='exclude')]
        title,description=cover.publication_text(info,rows+[rows[0]],[1])
        self.assertNotIn('部分比赛',title)
        self.assertIn('国王 VS 湖人',title)
        self.assertIn('Luka Doncic、Austin Reaves',title)
        self.assertEqual(description,'2026–27 NBA 季前赛，国王客场对阵湖人。\n比赛日期：2026.10.09')

    def test_publication_without_review_does_not_invent_player_stats(self):
        title,description=cover.publication_text(INFO)
        self.assertIn('2026.10.09',title)
        self.assertEqual(description,'2026–27 NBA 季前赛，国王客场对阵湖人。\n比赛日期：2026.10.09')
        self.assertNotIn('次',description)
        self.assertNotIn('东契奇',title)

    def test_publication_pending_keeps_brief_description_and_invalid_kind_rejected(self):
        row=dict(player='doncic',period='1',event_id='5',kind='score',status='pending')
        self.assertEqual(cover.publication_text(INFO,[row])[1],'2026–27 NBA 季前赛，国王客场对阵湖人。\n比赛日期：2026.10.09')
        row.update(status='keep',kind='unknown')
        with self.assertRaises(ValueError):cover.publication_text(INFO,[row])
    def test_official_colors_and_invalid_cache(self):
        colors=cover.team_colors()
        self.assertEqual(len(colors),30)
        self.assertEqual((colors['OKC'],colors['ATL']),('#007AC1','#E03A3E'))
        self.assertEqual((colors['LAL'],colors['SAC']),('#552583','#5A2D81'))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'assets'/'teams';folder.mkdir(parents=True)
            (folder/'colors.json').write_text(json.dumps(dict(colors={'LAL':'bad'})))
            with patch.object(cover.nh,'ROOT',root),self.assertRaisesRegex(ValueError,'颜色'):
                cover.team_colors()
    def test_official_date_season_and_away_left(self):
        data=cover.cover_info(INFO)
        self.assertEqual(data['date'],'2026.10.09')
        self.assertEqual(data['season'],'2026–27')
        self.assertEqual((data['away_name'],data['home_name']),('国王','湖人'))
        self.assertEqual(data['stage'],'季前赛')
    def test_summer_and_regular_use_game_id_season(self):
        info=copy.deepcopy(INFO);info['game']['gameTimeUTC']='2027-04-01T03:00:00Z';info['game']['gameLabel']='Regular Season'
        self.assertEqual(cover.cover_info(info)['season'],'2026–27')
        self.assertEqual(cover.cover_info(info)['stage'],'常规赛')
    def test_inconsistent_or_missing_metadata_rejected(self):
        for key,value in [('gameId','0012500036'),('gameTimeUTC','2026-02-30T02:30:00Z'),('gameTimeUTC',''),('gameTimeUTC','2026-10-09T02:30:00')]:
            info=copy.deepcopy(INFO);info['game'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):cover.cover_info(info)

    def test_beijing_midnight_boundary_offsets_and_cached_utc_fallback(self):
        for time,date in [('2026-10-08T15:59:59Z','2026.10.08'),('2026-10-08T16:00:00Z','2026.10.09'),('2026-12-31T16:00:00Z','2027.01.01'),('2026-10-08T19:30:00-07:00','2026.10.09')]:
            info=copy.deepcopy(INFO);info['game']['gameTimeUTC']=time
            with self.subTest(time=time):self.assertEqual(cover.cover_info(info)['date'],date)
        info=copy.deepcopy(INFO);info['game'].pop('gameTimeUTC');info['game_time_utc']='2026-10-09T02:30:00Z'
        self.assertEqual(cover.cover_info(info)['date'],'2026.10.09')

    def test_cached_logos_work_offline_and_corruption_or_fetch_failure_preserve_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'assets'/'teams';folder.mkdir(parents=True)
            urls=[f'https://cdn.nba.com/logos/nba/{i}/primary/L/logo.svg' for i in range(1610612737,1610612767)]
            (folder/'sources.json').write_text(json.dumps(dict(urls=urls)))
            svg=b'<svg xmlns="http://www.w3.org/2000/svg"/>'
            for i in range(1610612737,1610612767):(folder/f'{i}.svg').write_bytes(svg)
            with patch.object(cover.nh,'ROOT',root),patch.object(cover.urllib.request,'urlopen',side_effect=OSError('offline')) as fetch:
                self.assertEqual(cover.cache_logos(),folder)
                fetch.assert_not_called()
                bad=folder/'1610612737.svg';bad.write_bytes(b'broken')
                with self.assertRaisesRegex(ValueError,'损坏'):cover.cache_logos()
                fetch.assert_not_called()
                bad.unlink()
                with self.assertRaisesRegex(OSError,'offline'):cover.cache_logos()
                self.assertEqual((folder/'1610612747.svg').read_bytes(),svg)
