import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nba_highlight_maker import processing as processing


class AdjustTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.review=self.root/'review.csv'
        self.rows=[dict(event_id=str(event),player='doncic',kind='score',period='3',clock='PT07M48S',
                        description='shot',source=str(self.root/'Q3.mp4'),start='09:10',end='09:21',
                        status='pending',reason='two candidates',candidates='558;575',preview='',source_candidates='') for event in [326,327]]
        self.write()

    def write(self):
        with self.review.open('w',encoding='utf-8-sig',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=processing.FIELDS);writer.writeheader();writer.writerows(self.rows)

    def args(self,**changes):
        values=dict(event_id='326',start='09:15',end='09:25',player=None,review=str(self.review))
        values.update(changes)
        return SimpleNamespace(**values)

    def test_adjust_updates_only_selected_row_and_regenerates_preview(self):
        with patch.object(processing,'probe',return_value=600),patch.object(processing,'render_clip') as render:
            processing.adjust(self.args())
        with self.review.open(encoding='utf-8-sig',newline='') as file:updated=list(csv.DictReader(file))
        self.assertEqual((updated[0]['start'],updated[0]['end'],updated[0]['status']),('09:15','09:25','keep'))
        self.assertEqual(updated[1],self.rows[1])
        self.assertEqual(render.call_args.args[1:3],(555,565))
        self.assertEqual(render.call_args.args[3].name,'doncic_Q3_326.mp4')

    def test_bad_range_and_render_failure_leave_review_unchanged(self):
        old=self.review.read_bytes()
        with patch.object(processing,'probe',return_value=600),patch.object(processing,'render_clip') as render,self.assertRaises(ValueError):
            processing.adjust(self.args(end='20:00'))
        render.assert_not_called();self.assertEqual(old,self.review.read_bytes())
        with patch.object(processing,'probe',return_value=600),patch.object(processing,'render_clip',side_effect=RuntimeError('encoding failed')),self.assertRaises(RuntimeError):
            processing.adjust(self.args())
        self.assertEqual(old,self.review.read_bytes())

    def test_shared_event_requires_explicit_player(self):
        self.rows[1].update(event_id='326',player='reaves');self.write()
        with self.assertRaisesRegex(ValueError,'--player'):processing.adjust(self.args())
        with patch.object(processing,'probe',return_value=600),patch.object(processing,'render_clip'):
            processing.adjust(self.args(player='reaves'))
        with self.review.open(encoding='utf-8-sig',newline='') as file:updated=list(csv.DictReader(file))
        self.assertEqual(updated[0],self.rows[0]);self.assertEqual(updated[1]['status'],'keep')


if __name__=='__main__':unittest.main()
