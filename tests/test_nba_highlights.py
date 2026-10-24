import copy
import csv
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from nba_highlight_maker import processing as nh


def shot(identifier=1, person=1629029, clock="PT10M00.00S", description="Doncic Shot (Reaves 1 AST)"):
    return dict(actionId=identifier, period=1, personId=person, clock=clock,
                isFieldGoal=1, shotResult="Made", description=description,
                scoreHome="5", scoreAway="2", teamTricode="LAL")


def sample(t, clock, home=5, away=2, period=1):
    return dict(t=t, clock=clock, home=home, away=away, period=period)


class LogicTests(unittest.TestCase):
    def test_kessler_scores_assists_and_shared_play(self):
        actions=[shot(person=1631117,description='Kessler dunk (Doncic 1 AST)'),
                 shot(2,description='Doncic layup (Kessler 1 AST)'),
                 dict(shot(3,person=1631117),shotResult='Missed'),
                 dict(shot(4,person=1631117),isFieldGoal=0)]
        events=nh.select_events(dict(actions=actions),{1})
        self.assertEqual([(e['event_id'],e['kind']) for e in events if e['player']=='kessler'],
                         [('1','score'),('2','assist')])
        self.assertEqual(nh.summarize_events(events)['kessler'],dict(scores=1,assists=1,clips=2))
        row=dict(events[1],start='0',end='5',status='pending',source='video.mp4',preview='',reason='review',candidates='2')
        self.assertIn('Walker Kessler',nh.describe_clip(row,1,1))

    def test_video_time_accepts_timecodes_and_legacy_seconds(self):
        for raw,expected in [('09:15',555),('9:15.5',555.5),('01:09:15.125',4155.125),
                             ('90:00',5400),('555',555),(555.5,555.5),(' 00:00 ',0)]:
            self.assertEqual(nh.parse_video_time(raw),expected)
        for raw in ['09:60','1:60:00','nan','inf','-1','abc','','9:15:99']:
            with self.assertRaises(ValueError):nh.parse_video_time(raw)

    def test_clock_formats_and_reject_shot_clock(self):
        for raw, expected in [("PT11M06.00S", 666), ("11:06", 666), ("40.9", 40.9), ("00:00", 0)]:
            self.assertEqual(nh.parse_clock(raw), expected)
        for raw in ["16", "12:61", "PT13M00S", "nan", "-1.0"]:
            with self.assertRaises(ValueError):
                nh.parse_clock(raw)

    def test_field_goals_assists_and_shared_event(self):
        made = shot()
        missed = dict(shot(2), shotResult="Missed")
        free_throw = dict(shot(3), isFieldGoal=0)
        other = shot(4, person=999, description="LaRavia Shot (Doncic 1 AST)")
        events = nh.select_events(dict(gameId="0012600036", actions=[made, made, missed, free_throw, other]), {1})
        self.assertEqual([(e["player"], e["kind"]) for e in events],
                         [("doncic", "score"), ("reaves", "assist"), ("doncic", "assist")])

    def test_unicode_assist_and_missing_quarter(self):
        events = nh.select_events(dict(actions=[shot(description="Shot (Dončić 2 AST)"),
                                               dict(shot(2), period=2)]), {1})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "score+assist")

    def test_merge_only_same_file_and_overlap(self):
        rows = [("Q1", 0, 10), ("Q1", 8, 14), ("Q1", 20, 25), ("Q2", 0, 8)]
        self.assertEqual(nh.merge_ranges(rows), [("Q1", 0, 14), ("Q1", 20, 25), ("Q2", 0, 8)])

    def test_live_clock_with_delayed_score(self):
        data = [sample(0, 602, 3), sample(1, 601, 3), sample(2, 600, 3),
                sample(3, 599), sample(4, 598)]
        result = nh.locate_event(shot(), data)
        self.assertEqual(result["status"], "keep")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertAlmostEqual(result["candidates"][0], 2)

    def test_missing_score_unique_clock(self):
        data = [sample(i, 602-i, None, None) for i in range(5)]
        self.assertEqual(nh.locate_event(shot(), data)["status"], "keep")

    def test_score_animation_delay(self):
        data = [sample(i,602-i,3 if i < 8 else 5) for i in range(11)]
        self.assertEqual(nh.locate_event(shot(),data)["status"],"keep")

    def test_pause_is_pending(self):
        data = [sample(0, 601), sample(1, 600), sample(2, 600), sample(3, 600), sample(4, 600)]
        self.assertEqual(nh.locate_event(shot(), data)["status"], "pending")

    def test_replay_duplicate_is_pending(self):
        data = [sample(i, 602-i) for i in range(5)] + [sample(20+i, 602-i) for i in range(5)]
        self.assertEqual(nh.locate_event(shot(), data)["status"], "pending")

    def test_wrong_score_period_and_jump_not_accepted(self):
        for data in [[sample(i, 602-i, 99, 99) for i in range(5)],
                     [sample(i, 602-i, period=2) for i in range(5)],
                     [sample(0, 610), sample(1, 600), sample(2, 590)]]:
            self.assertEqual(nh.locate_event(shot(), data)["status"], "pending")

    def test_time_boundaries_and_unresolved_export(self):
        self.assertEqual(nh.clip_bounds(2, 10), (0, 5))
        for start, end in [(-1, 5), (5, 5), (0, 11), (float("nan"), 5)]:
            with self.assertRaises(ValueError):
                nh.validate_range(start, end, 10)
        with self.assertRaises(ValueError):
            nh.validate_review([dict(status="pending")])

    def test_ocr_game_clock_and_score_fields(self):
        raw = dict(clock=[dict(text="1 1:06")], period=[dict(text="1ST")],
                   home=[dict(text="LAL"), dict(text="3")], away=[dict(text="SAC 2")])
        parsed = nh.parse_ocr(raw, 120)
        self.assertEqual((parsed["clock"], parsed["period"], parsed["home"], parsed["away"]), (666, 1, 3, 2))
        raw["clock"] = [dict(text="11:06 16")]
        self.assertIsNone(nh.parse_ocr(raw, 120)["clock"])

    def test_review_preserves_manual_changes_and_adds_quarter(self):
        existing = [dict(player="doncic", period="1", event_id="5", start="80", end="98", status="keep")]
        fresh = [dict(player="doncic", period=1, event_id="5", start=85, end=96, status="pending"),
                 dict(player="doncic", period=2, event_id="99", start=10, end=21, status="keep")]
        merged = nh.preserve_review(existing, fresh)
        self.assertEqual(merged[0], existing[0])
        self.assertEqual(len(merged), 2)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = dict(gameId="0012600036", actions=[shot()])
        self.info = dict(game_id="0012600036", home="LAL", away="SAC")

    def test_cache_reuse_and_refresh(self):
        with patch.object(nh, "fetch_game", return_value=(self.data, self.info)) as fetch:
            nh.load_game(self.root, "0012600036")
            nh.load_game(self.root, "0012600036")
            fetch.assert_called_once()
            nh.load_game(self.root, "0012600036", refresh=True)
            self.assertEqual(fetch.call_count, 2)

    def test_bad_cache_requires_explicit_refresh(self):
        self.root.joinpath("play_by_play.json").write_text("broken")
        with patch.object(nh, "fetch_game") as fetch, self.assertRaises(ValueError):
            nh.load_game(self.root, "0012600036")
        fetch.assert_not_called()

    def test_refresh_failure_preserves_old_cache(self):
        with patch.object(nh, "fetch_game", return_value=(self.data, self.info)):
            nh.load_game(self.root, "0012600036")
        old = self.root.joinpath("play_by_play.json").read_bytes()
        with patch.object(nh, "fetch_game", side_effect=OSError("offline")), self.assertRaises(OSError):
            nh.load_game(self.root, "0012600036", refresh=True)
        self.assertEqual(old, self.root.joinpath("play_by_play.json").read_bytes())

    def test_wrong_game_id(self):
        with patch.object(nh, "fetch_game", return_value=(self.data, self.info)):
            nh.load_game(self.root, "0012600036")
        data = copy.deepcopy(self.data)
        data["gameId"] = "0012600028"
        self.root.joinpath("play_by_play.json").write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            nh.load_game(self.root, "0012600036")

    def test_wrong_cache_type_and_invalid_refresh(self):
        with patch.object(nh, "fetch_game", return_value=(self.data,self.info)):
            nh.load_game(self.root,"0012600036")
        old=self.root.joinpath("play_by_play.json").read_bytes()
        with patch.object(nh,"fetch_game",return_value=([],self.info)), self.assertRaises(ValueError):
            nh.load_game(self.root,"0012600036",refresh=True)
        self.assertEqual(old,self.root.joinpath("play_by_play.json").read_bytes())
        self.root.joinpath("play_by_play.json").write_text("[]")
        with self.assertRaises(ValueError):
            nh.load_game(self.root,"0012600036")


class ExportTests(unittest.TestCase):
    def test_timecode_review_exports_numeric_ranges_and_compares_legacy_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'Q3.mp4';source.write_bytes(b'fixture')
            rows=[dict(event_id='1',player=p,kind='score',period='3',clock='PT07M48S',description='test',
                       source=str(source),start='09:15',end='09:25',status='keep',reason='',candidates='558',preview='')
                  for p in nh.PLAYERS]
            review=root/'review.csv'
            for filename,data in [('review.csv',rows),('candidates.csv',[dict(r,start='555',end='565') for r in rows])]:
                with (root/filename).open('w',newline='') as file:
                    writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerows(data)
            def render(source,start,end,destination,progress=None,watermark=False):
                self.assertEqual(watermark,Path(source).name=='Q3.mp4')
                self.assertIn((start,end),[(0,136),(0,4),(555,565)]);destination.write_bytes(b'clip')
                self.assertEqual(progress.total,end-start);progress.update(end-start)
            def concatenate(command,progress=None):
                self.assertEqual(progress.total,10);progress.update(10)
                self.assertEqual(Path(command[command.index('-i')+1]).read_text(),"file '0000.mp4'\n")
                Path(command[-1]).write_bytes(b'final');return ''
            with patch('nba_highlight_maker.cover.generate_cover',return_value=root/'cover.png') as cover,patch.object(nh,'ROOT',root),patch.object(nh,'probe',side_effect=lambda p:{'lakers-intro.mp4':4,'la-scenery.mp4':136}.get(Path(p).name,1000)),patch.object(nh,'render_clip',side_effect=render),patch.object(nh,'run',side_effect=concatenate):
                nh.export(SimpleNamespace(review=str(review)))
            cover.assert_called_once_with(root.resolve(),review=review.resolve(),players=list(nh.PLAYERS))
            metadata=json.loads((root/'export.json').read_text())
            self.assertEqual(metadata['manual_time_edits'],0)
            self.assertEqual(metadata['shared_duplicates'],len(nh.PLAYERS)-1)
            self.assertEqual(metadata['merged_clips'],1)
            self.assertEqual([Path(p['source']).name for p in metadata['opening_segments']],[])
            self.assertEqual((root/'highlights.mp4').read_bytes(),b'final')

    def test_output_cannot_overwrite_a_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/"highlights.mp4"
            source.write_bytes(b"source must survive")
            row=dict(event_id="1",player="doncic",kind="score",period="1",clock="PT10M00S",
                     description="test",source=str(source),start="0",end="5",status="keep",reason="",candidates="",preview="")
            review=root/"review.csv"
            with review.open("w",newline="") as file:
                writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
            with patch.object(nh,"render_clip") as render, self.assertRaisesRegex(ValueError,"源录像"):
                nh.export(SimpleNamespace(review=str(review)))
            render.assert_not_called()
            self.assertEqual(source.read_bytes(),b"source must survive")


if __name__ == "__main__":
    unittest.main()
