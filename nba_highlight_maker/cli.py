#!/usr/bin/env python3
"""NBA 录像下载、归档、集锦分析与导出的统一命令入口。"""
import argparse
import sys

from nba_highlight_maker import download as download
from nba_highlight_maker import processing as processing
from nba_highlight_maker import cover as covers
from nba_highlight_maker import review as review_console
from nba_highlight_maker import games


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    listing=commands.add_parser('games',help='查询比赛 Game ID 和对阵（北京时间）')
    listing.add_argument('--date',help='北京时间日期 YYYY-MM-DD；默认今天')
    listing.add_argument('--team',nargs='+',default=[],help='英文球队代码，如 LAL、LAC（不区分大小写）；多个球队匹配任意一队')
    listing.set_defaults(handler=games.list_games)
    downloading=commands.add_parser("download",help="下载、识别节次并归档")
    downloading.add_argument("urls",nargs="+",help="一个或多个完整微博或 YouTube HTTPS 地址")
    downloading.add_argument("--game-id",help="默认使用当前唯一缓存比赛；多场比赛时必须指定")
    downloading.add_argument("--game-dir",help="默认 ~/Movies/NBA/games/比赛日期_主队_vs_客队")
    downloading.add_argument("--refresh-data",action="store_true")
    downloading.add_argument('--source-type',choices=['recording','highlights'],default='recording',help='完整录像或剪辑集锦；集锦必须显式指定')
    downloading.add_argument('--ocr-profile',choices=['espn','spectrum'],default='espn',help='计分牌校准，与下载平台无关')
    downloading.set_defaults(handler=download.download_all)
    analysis=commands.add_parser("analyze",help="生成候选片段和审核 CSV")
    analysis.add_argument("--game-dir",required=True)
    analysis.add_argument("--game-id",required=True)
    analysis.add_argument("--refresh-data",action="store_true")
    analysis.add_argument('--output-dir',help='独立分析目录，避免与已有人工审核表混用')
    analysis.add_argument('--players',nargs='+',help='本场出场球员 ID；省略时必须已有保存的球员选择')
    analysis.set_defaults(handler=processing.analyze)
    adjusting=commands.add_parser('adjust',help='按事件编号修改区间、确认保留并更新单段预览')
    adjusting.add_argument('event_id',help='日志或审核表中的事件编号，不是进度序号')
    adjusting.add_argument('start',help='源录像起点，如 09:15')
    adjusting.add_argument('end',help='源录像终点，如 09:25')
    adjusting.add_argument('--player',help='审核表中的球员键或编号')
    adjusting.add_argument('--review',help='默认使用 output 中唯一的 review.csv')
    adjusting.add_argument('--source',help='改用另一份源录像，时间按该录像填写')
    adjusting.set_defaults(handler=processing.adjust)
    marking=commands.add_parser('mark',help='人工记录缺失、排除或恢复待确认')
    marking.add_argument('event_id')
    marking.add_argument('--status',choices=['missing','exclude','pending'],required=True)
    marking.add_argument('--reason',required=True,help='人工判断的依据；缺失应核查所有提供的源视频')
    marking.add_argument('--player')
    marking.add_argument('--review')
    marking.set_defaults(handler=processing.mark)
    reviewing=commands.add_parser('review',help='打开本地 NBA 审核控制台')
    reviewing.add_argument('--review',help='默认列出 output 内全部审核表；指定此项选中该表')
    reviewing.add_argument('--port',type=int,default=8767)
    reviewing.add_argument('--no-open',action='store_true',help='启动服务，不自动打开浏览器')
    reviewing.set_defaults(handler=review_console.serve)
    exporting=commands.add_parser("export",help="合并审核片段，可选前置视频，导出一条 MP4")
    exporting.add_argument("--review",required=True)
    exporting.add_argument('--players',nargs='+',help='只导出所选出场球员的回合')
    exporting.add_argument('--prepend',nargs='+',metavar='VIDEO',help='按顺序拼接到集锦前的本地视频；默认不拼接')
    exporting.add_argument('--scenery',action=argparse.BooleanOptionalAction,default=False,help='兼容旧命令：拼接本地 intro/la-scenery.mp4，默认关闭')
    exporting.add_argument('--watermark',action=argparse.BooleanOptionalAction,default=True,help='比赛片段添加 NBA 2K 水印；--no-watermark 关闭')
    exporting.set_defaults(handler=processing.export)
    covering=commands.add_parser('cover',help='生成16:9、4:3、3:4封面及投稿标题、简介')
    covering.add_argument('--game-id',required=True)
    covering.set_defaults(handler=covers.cover)
    args=parser.parse_args(argv)
    try:
        args.handler(args)
    except (OSError,ValueError,RuntimeError,KeyError) as error:
        print("错误：",error,file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    sys.exit(main())
