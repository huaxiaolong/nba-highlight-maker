import unittest
import csv
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nba_highlight_maker import processing as processing
from nba_highlight_maker.players import roster, select_keys


def game_info():
    return {'game': {
        'homeTeam': {'teamTricode': 'LAL', 'players': [
            {'personId': 1629029, 'firstName': 'Luka', 'familyName': 'Doncic', 'statistics': {'minutes': '30:00'}},
            {'personId': 100, 'firstName': 'New', 'familyName': 'Player', 'statistics': {'minutes': '00:01'}},
            {'personId': 101, 'firstName': 'Did', 'familyName': 'NotPlay', 'statistics': {'minutes': ''}},
            {'personId': 102, 'firstName': 'Zero', 'familyName': 'Minutes', 'statistics': {'minutes': 'PT00M00.00S'}}]},
        'awayTeam': {'teamTricode': 'GSW', 'players': [
            {'personId': 201939, 'firstName': 'Stephen', 'familyName': 'Curry', 'statistics': {'minutes': 'PT32M15S'}}]}}}


class PlayerTests(unittest.TestCase):
    def test_analysis_requires_selection_before_ocr_and_reuses_saved_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Q1.mp4').write_bytes(b'video')
            output=root/'output'/'0022600102'
            info=dict(game_info(),home='LAL',away='GSW')
            args=SimpleNamespace(game_id='0022600102',game_dir=str(root),refresh_data=False)
            with patch.object(processing,'ROOT',root),patch.object(processing,'load_game',return_value=(dict(actions=[]),info)),patch.object(processing,'vision_binary') as vision:
                with self.assertRaisesRegex(ValueError,'先选择球员'):
                    processing.analyze(args)
                vision.assert_not_called()
                self.assertFalse((output/'events.json').exists())
                processing.atomic_json(output/'players.json',dict(selected_players=['201939']))
                with patch.object(processing,'select_events',return_value=[]) as events,patch.object(processing,'vision_binary',side_effect=RuntimeError('stop before OCR')):
                    with self.assertRaisesRegex(RuntimeError,'stop before OCR'):
                        processing.analyze(args)
                self.assertEqual(list(events.call_args.args[2]),['201939'])

    def test_export_only_selected_players_and_rejects_empty_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4';source.write_bytes(b'source')
            rows=[]
            for key,status in [('201939','keep'),('100','pending')]:
                row=dict.fromkeys(processing.FIELDS,'')
                row.update(player=key,status=status,event_id='1',period='1',clock='PT10M00S',kind='score',source=str(source),start='1',end='5')
                rows.append(row)
            review=root/'review.csv'
            with review.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=processing.FIELDS);writer.writeheader();writer.writerows(rows)
            (root/'source.json').write_text(json.dumps(game_info()))
            def render(source,start,end,destination,**kwargs):destination.write_bytes(b'clip')
            def run(command,**kwargs):Path(command[-1]).write_bytes(b'final')
            args=SimpleNamespace(review=str(review),players=['201939'],scenery=False,watermark=False)
            with patch.object(processing,'probe_frame_rate',return_value='25'),patch.object(processing,'probe',return_value=10),patch.object(processing,'render_clip',side_effect=render),patch.object(processing,'run',side_effect=run),patch('nba_highlight_maker.cover.generate_cover',return_value=root/'cover.png') as cover:
                processing.export(args)
                cover.assert_called_once_with(root.resolve(),review=review.resolve(),players=['201939'])
                metadata=json.loads((root/'export.json').read_text())
                self.assertEqual(list(metadata['players']),['201939'])
                self.assertEqual(metadata['selected_players'],['201939'])
                self.assertEqual(metadata['unique_events'],1)
                for keys in [[],['100'],['101']]:
                    args.players=keys
                    with self.assertRaises(ValueError):processing.export(args)
                self.assertEqual((root/'highlights.mp4').read_bytes(),b'final')
                processing.adjust(SimpleNamespace(review=str(review),event_id='1',player='100',start='2',end='4'))
                with review.open(encoding='utf-8-sig',newline='') as f:updated=list(csv.DictReader(f))
                self.assertEqual(next(r['status'] for r in updated if r['player']=='100'),'keep')

    def test_arbitrary_three_players_are_not_called_lakers_core(self):
        import copy
        from nba_highlight_maker import cover as nba_cover
        from tests.test_cover import INFO
        info=copy.deepcopy(INFO)
        for side in ('homeTeam','awayTeam'):
            info['game'][side]['players']=game_info()['game'][side]['players']
        rows=[dict(player=p,period='1',event_id='1',kind='score',status='keep') for p in ('201939','doncic','100')]
        title,_=nba_cover.publication_text(info,rows)
        short,_=nba_cover.publication_text(info,rows,douyin=True)
        self.assertNotIn('湖人三核',title)
        self.assertNotIn('湖人三核',short)
        self.assertLessEqual(len(short),30)

    def test_roster_includes_both_teams_and_only_played_players(self):
        players=roster(game_info())
        self.assertEqual(list(players), ['201939', 'doncic', '100'])
        self.assertEqual(players['201939']['name'], 'Stephen Curry')
        self.assertEqual(players['doncic']['name'], 'Luka Doncic')
        self.assertEqual(players['100']['team'], 'LAL')
        self.assertEqual(select_keys(players, ['1629029', '201939', 'doncic']), ['doncic', '201939'])
        for values in [[], ['101'], ['999'], 'doncic', [True]]:
            with self.subTest(values=values),self.assertRaises(ValueError):select_keys(players,values)

    def test_dynamic_score_assist_and_shared_play(self):
        players=roster(game_info())
        actions=[dict(actionId=1, period=1, clock='PT10M00S', personId=100,
                      assistPersonId=1629029, isFieldGoal=1, shotResult='Made', description='Player dunk'),
                 dict(actionId=2, period=1, clock='PT09M00S', personId=201939,
                      isFieldGoal=1, shotResult='Made', description='Curry three')]
        catalog={key:(p['id'],p['last_name']) for key,p in players.items()}
        events=processing.select_events(dict(actions=actions),{1},catalog)
        self.assertEqual([(e['player'],e['kind']) for e in events],
                         [('doncic','assist'),('100','score'),('201939','score')])
        self.assertEqual(processing.summarize_events(events,catalog)['100']['scores'],1)

    def test_assist_id_wins_and_ambiguous_surname_is_not_guessed(self):
        action=dict(actionId=1,period=1,clock='PT10M00S',personId=10,assistPersonId=20,
                    isFieldGoal=1,shotResult='Made',description='Shot (Other 1 AST)')
        catalog={'10':(10,'Other'),'20':(20,'Williams'),'30':(30,'Williams')}
        events=processing.select_events(dict(actions=[action]),{1},catalog)
        self.assertEqual([(e['player'],e['kind']) for e in events],[('10','score'),('20','assist')])
        del action['assistPersonId'];action['description']='Shot (Williams 1 AST)'
        events=processing.select_events(dict(actions=[action]),{1},catalog)
        self.assertEqual([(e['player'],e['kind']) for e in events],[('10','score')])

    def test_old_metadata_remains_readable_but_explicit_empty_roster_is_not_guessed(self):
        self.assertIn('doncic',roster({}))
        self.assertEqual([p['name'] for p in roster({}).values()], ['Luka Doncic','Austin Reaves','Walker Kessler'])
        self.assertEqual(roster({'game':{'homeTeam':{'players':[]},'awayTeam':{'players':[]}}}),{})
