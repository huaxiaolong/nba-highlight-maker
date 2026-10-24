import contextlib
import datetime as dt
import io
import json
import unittest
from unittest.mock import patch

from nba_highlight_maker import cli, games


def game(identifier='0022600005', time='2026-10-23T23:00:00Z', away='LAL', home='LAC'):
    return dict(gameId=identifier, gameTimeUtc=time, gameStatus=3,
                awayTeam=dict(teamTricode=away), homeTeam=dict(teamTricode=home))


class GamesTests(unittest.TestCase):
    def test_beijing_day_boundaries_deduplicate_and_sort(self):
        early=game('0022600001', '2026-10-23T16:00:00Z')
        late=game('0022600002', '2026-10-24T15:59:59Z')
        outside=[game(time='2026-10-23T15:59:59Z'), game(time='2026-10-24T16:00:00Z')]
        rows=games.select_games([late, early, early, *outside], dt.date(2026,10,24))
        self.assertEqual([row['gameId'] for row in rows], ['0022600001','0022600002'])

    def test_team_codes_filter_either_side(self):
        rows=[game(),game('0022600006',away='GSW',home='BOS')]
        for names in [['LAL'],['lac'],['LAL','GSW']]:
            with self.subTest(names=names):
                expected=2 if len(names)==2 else 1
                self.assertEqual(len(games.select_games(rows, dt.date(2026,10,24), names)),expected)
        self.assertEqual(games.select_games(rows, dt.date(2026,10,24), ['MIA']),[])
        for name in ['湖人','洛杉矶','XXX']:
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'球队代码'):
                games.select_games(rows,dt.date(2026,10,24),[name])

    def test_invalid_game_data_is_an_error(self):
        for fields in [dict(gameId='123'),dict(gameTimeUtc='2026-10-24T07:00:00'),dict(homeTeam=None)]:
            with self.subTest(fields=fields),self.assertRaisesRegex(ValueError,'比赛数据'):
                games.select_games([dict(game(),**fields)],dt.date(2026,10,24))

    def test_fetch_checks_date_and_page_structure(self):
        page=dict(selectedDate='2026-10-23',hasError=False,
                  gameCardFeed=dict(nextPageToken=None,modules=[dict(cards=[dict(cardData=game())])]))
        def response(data):
            html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(dict(props=dict(pageProps=data)))+'</script>'
            return html.encode()
        with patch.object(games.urllib.request,'urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value=response(page)
            self.assertEqual(games.fetch_games(dt.date(2026,10,23)),[game()])
            self.assertIn('date=2026-10-23',request.call_args.args[0].full_url)
            for invalid in [dict(page,selectedDate='2026-10-24'),dict(page,hasError=True),{},
                            dict(page,gameCardFeed={}),dict(page,gameCardFeed=None),
                            dict(page,gameCardFeed=dict(page['gameCardFeed'],nextPageToken='more'))]:
                request.return_value.__enter__.return_value.read.return_value=response(invalid)
                with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                    games.fetch_games(dt.date(2026,10,23))

    def test_cli_fetches_both_days_and_prints_copyable_id(self):
        with patch.object(games,'fetch_games',side_effect=[[game()],[]]) as fetch,contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['games','--date','2026-10-24','--team','LAL']),0)
        self.assertEqual([c.args[0] for c in fetch.call_args_list],[dt.date(2026,10,23),dt.date(2026,10,24)])
        self.assertIn('0022600005',out.getvalue())
        self.assertIn('湖人(LAL) @ 快船(LAC)',out.getvalue())
        self.assertIn('07:00',out.getvalue())

    def test_default_today_and_no_games(self):
        with patch.object(games,'today',return_value=dt.date(2026,10,24)),patch.object(games,'fetch_games',return_value=[]),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['games']),0)
        self.assertIn('2026-10-24',out.getvalue())
        self.assertIn('没有符合条件的比赛',out.getvalue())

    def test_invalid_input_and_network_failure_have_nonzero_exit(self):
        for argv in [['--date','2026-02-30'],['--date','20261024'],['--date','0001-01-01'],['--team','湖人']]:
            with self.subTest(argv=argv),patch.object(games,'fetch_games') as fetch,contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['games',*argv]),1)
                fetch.assert_not_called()
        with patch.object(games,'fetch_games',side_effect=OSError('网络不可用')),contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(cli.main(['games']),1)
        self.assertIn('网络不可用',out.getvalue())


if __name__=='__main__':
    unittest.main()
