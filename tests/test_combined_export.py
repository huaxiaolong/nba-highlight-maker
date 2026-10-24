import unittest
import csv
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from nba_highlight_maker import processing as nh


class CombinedRangesTests(unittest.TestCase):
    def test_shared_event_unions_overlapping_review_bounds(self):
        entries=[(1,'Q1',12,23,5),(1,'Q1',10,21,5),(1,'Q1',10,21,5)]
        self.assertEqual(nh.combine_ranges(entries),[(1,'Q1',10,23)])

    def test_sort_and_merge_overlap_chains(self):
        entries=[(2,'Q2',0,5,100),(1,'Q1',12,20,3),(1,'Q1',0,10,1),(1,'Q1',8,14,2)]
        self.assertEqual(nh.combine_ranges(entries),[(1,'Q1',0,20),(2,'Q2',0,5)])

    def test_full_game_period_boundaries_and_overtime_are_preserved(self):
        entries=[(6,'FULL',200,210,600),(2,'FULL',10,20,200),(1,'FULL',0,10,100),(5,'FULL',100,110,500)]
        self.assertEqual(nh.combine_ranges(entries),[(1,'FULL',0,10),(2,'FULL',10,20),(5,'FULL',100,110),(6,'FULL',200,210)])

    def test_multiple_sources_follow_official_event_order(self):
        entries=[(1,'A',0,10,20),(1,'Z',30,40,5),(1,'A',20,30,30)]
        self.assertEqual(nh.combine_ranges(entries),[(1,'Z',30,40),(1,'A',0,10),(1,'A',20,30)])

    def test_shared_event_conflicting_sources_or_disjoint_ranges_fail(self):
        for second in [(1,'OTHER',0,10,5),(1,'Q1',11,20,5)]:
            with self.subTest(second=second),self.assertRaisesRegex(ValueError,'同一回合'):
                nh.combine_ranges([(1,'Q1',0,10,5),second])

    def test_single_clip_and_empty_selection(self):
        self.assertEqual(nh.combine_ranges([]),[])
        self.assertEqual(nh.combine_ranges([(1,'Q1',0,1,1)]),[(1,'Q1',0,1)])


class CombinedExportTests(unittest.TestCase):
    def test_optional_opening_and_watermark_are_independent(self):
        for prepend in [None, ['custom.mp4'], ['custom.mp4', 'second.mov']]:
            for watermark in [False,True]:
                with self.subTest(prepend=prepend,watermark=watermark),tempfile.TemporaryDirectory() as folder:
                    root=Path(folder).resolve();review=root/'review.csv'
                    row=dict.fromkeys(nh.FIELDS,'')
                    row.update(event_id='1',period='1',player='doncic',source=str(root/'Q1.mp4'),start='0',end='5',status='keep')
                    with review.open('w',newline='') as file:
                        writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
                    paths=[str(root/name) for name in prepend] if prepend else None
                    def probe(path,**kwargs):
                        self.assertNotIn(Path(path).name,['la-scenery.mp4','lakers-intro.mp4'])
                        return 4 if str(path) in (paths or []) else 10
                    def concat(command,progress=None):Path(command[-1]).write_bytes(b'final');return ''
                    with patch('nba_highlight_maker.cover.generate_cover',return_value=root/'cover.png'),patch.object(nh,'ROOT',root),patch.object(nh,'probe',side_effect=probe),patch.object(nh,'render_clip') as render,patch.object(nh,'run',side_effect=concat):
                        nh.export(SimpleNamespace(review=str(review),prepend=paths,watermark=watermark))
                    self.assertEqual([Path(c.args[0]).name for c in render.call_args_list],(prepend or [])+['Q1.mp4'])
                    self.assertEqual([c.kwargs['watermark'] for c in render.call_args_list],[False]*len(prepend or [])+[watermark])
                    metadata=json.loads((root/'export.json').read_text())
                    self.assertEqual(metadata['prepend'],paths or [])
                    self.assertEqual(metadata['watermark']['enabled'],watermark)
                    self.assertEqual(metadata['expected_duration_seconds'],5+4*len(prepend or []))

    def test_opening_cannot_be_the_output_and_failed_probe_preserves_output(self):
        for use_output in [True,False]:
            with self.subTest(use_output=use_output),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();review=root/'review.csv';output=root/'highlights.mp4'
                output.write_bytes(b'old')
                row=dict.fromkeys(nh.FIELDS,'')
                row.update(event_id='1',period='1',player='doncic',source=str(root/'Q1.mp4'),start='0',end='5',status='keep')
                with review.open('w',newline='') as file:
                    writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
                def probe(path,**kwargs):
                    if Path(path).name=='bad.mp4':raise ValueError('invalid video')
                    return 10
                with patch.object(nh,'probe',side_effect=probe),patch.object(nh,'render_clip') as render:
                    with self.assertRaises(ValueError):nh.export(SimpleNamespace(review=str(review),prepend=[str(output if use_output else root/'bad.mp4')]))
                render.assert_not_called()
                self.assertEqual(output.read_bytes(),b'old')

    def test_validation_and_failed_concat_preserve_existing_output(self):
        for status in ['pending','exclude','keep']:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as folder:
                root=Path(folder).resolve();review=root/'review.csv';output=root/'highlights.mp4'
                output.write_bytes(b'old export')
                row=dict.fromkeys(nh.FIELDS,'')
                row.update(event_id='1',period='1',player='doncic',source=str(root/'Q1.mp4'),start='0',end='5',status=status)
                with review.open('w',newline='') as file:
                    writer=csv.DictWriter(file,fieldnames=nh.FIELDS);writer.writeheader();writer.writerow(row)
                before=review.read_bytes()
                with patch('nba_highlight_maker.cover.generate_cover',return_value=root/'cover.png'),patch.object(nh,'ROOT',root),patch.object(nh,'probe',return_value=10),patch.object(nh,'render_clip') as render,patch.object(nh,'run',side_effect=RuntimeError('concat failed')):
                    with self.assertRaises((ValueError,RuntimeError)):
                        nh.export(SimpleNamespace(review=str(review)))
                self.assertEqual(output.read_bytes(),b'old export')
                self.assertEqual(review.read_bytes(),before)
                self.assertEqual(render.call_count,1 if status=='keep' else 0)
