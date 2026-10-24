import contextlib
import io
import unittest
from unittest.mock import patch

from nba_highlight_maker import cli as cli


class CLITests(unittest.TestCase):
    def test_cover_does_not_encode_video(self):
        with patch.object(cli.covers,'cover') as cover,patch.object(cli.processing,'export') as export:
            self.assertEqual(cli.main(['cover','--game-id','0012600036']),0)
        self.assertEqual(cover.call_args.args[0].game_id,'0012600036')
        export.assert_not_called()
    def test_adjust_routes_to_review_module_without_exporting(self):
        with patch.object(cli.processing,'adjust') as adjust,patch.object(cli.processing,'export') as export:
            self.assertEqual(cli.main(['adjust','326','09:15','09:25']),0)
        args=adjust.call_args.args[0]
        self.assertEqual((args.event_id,args.start,args.end),('326','09:15','09:25'))
        self.assertIsNone(args.review)
        export.assert_not_called()
    def test_highlights_and_mark_options_reach_handlers(self):
        with patch.object(cli.download,'download_all') as download:
            self.assertEqual(cli.main(['download','https://youtu.be/abc','--source-type','highlights','--ocr-profile','spectrum']),0)
        args=download.call_args.args[0]
        self.assertEqual((args.source_type,args.ocr_profile),('highlights','spectrum'))
        with patch.object(cli.processing,'mark') as mark:
            self.assertEqual(cli.main(['mark','87','--status','missing','--reason','人工确认未收录']),0)
        self.assertEqual(mark.call_args.args[0].status,'missing')

    def test_review_starts_local_console_without_export(self):
        with patch.object(cli.review_console,'serve') as serve,patch.object(cli.processing,'export') as export:
            self.assertEqual(cli.main(['review','--review','/tmp/review.csv','--no-open']),0)
        args=serve.call_args.args[0]
        self.assertEqual((args.review,args.port,args.no_open),('/tmp/review.csv',8767,True))
        export.assert_not_called()

    def test_export_boolean_options_keep_defaults_and_allow_independent_changes(self):
        cases=[([],False,True),(['--no-scenery'],False,True),(['--no-watermark'],False,False),(['--no-scenery','--no-watermark'],False,False),(['--scenery','--watermark'],True,True)]
        for flags,scenery,watermark in cases:
            with self.subTest(flags=flags),patch.object(cli.processing,'export') as export:
                self.assertEqual(cli.main(['export','--review','/tmp/review.csv',*flags]),0)
            args=export.call_args.args[0]
            self.assertEqual((args.scenery,args.watermark),(scenery,watermark))

    def test_export_accepts_ordered_local_opening_paths(self):
        with patch.object(cli.processing,'export') as export:
            self.assertEqual(cli.main(['export','--review','/tmp/review.csv','--prepend','/tmp/first clip.mov','/tmp/second.mp4']),0)
        self.assertEqual(export.call_args.args[0].prepend,['/tmp/first clip.mov','/tmp/second.mp4'])

    def test_each_command_calls_only_its_module(self):
        cases=[(["download","https://weibo.com/123/test"],"download",None),
               (["analyze","--game-dir","/tmp/game","--game-id","0012600036"],"analyze","0012600036"),
               (["export","--review","/tmp/review.csv"],"export",None)]
        for argv,selected,game_id in cases:
            with self.subTest(command=selected), patch.object(cli.download,"download_all") as download, patch.object(cli.processing,"analyze") as analyze, patch.object(cli.processing,"export") as export:
                self.assertEqual(cli.main(argv),0)
                handlers={"download":download,"analyze":analyze,"export":export}
                for name,handler in handlers.items():
                    self.assertEqual(handler.call_count,int(name==selected))
                args=handlers[selected].call_args.args[0]
                if selected!="export":
                    self.assertEqual(args.game_id,game_id)
                    self.assertFalse(args.refresh_data)

    def test_download_errors_keep_nonzero_exit_status(self):
        with patch.object(cli.download,"download_all",side_effect=ValueError("invalid URL")),contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(cli.main(["download","bad-url"]),1)
        self.assertIn("invalid URL",output.getvalue())

    def test_removed_run_is_rejected_before_any_action(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            cli.main(["run","https://weibo.com/123/test"])
        self.assertEqual(error.exception.code,2)


if __name__=="__main__":
    unittest.main()
