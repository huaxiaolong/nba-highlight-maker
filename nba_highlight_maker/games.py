"""按北京时间查询 NBA 官方比赛列表。"""
import datetime as dt
import json
import re
import urllib.request

from nba_highlight_maker.cover import NAMES

BEIJING=dt.timezone(dt.timedelta(hours=8))


def today():
    return dt.datetime.now(BEIJING).date()


def team_codes(names):
    codes=set()
    for name in names:
        code=name.strip().upper()
        if code not in NAMES:
            raise ValueError(f'未知球队代码：{name}；请使用英文球队代码，例如 LAL、LAC')
        codes.add(code)
    return codes


def fetch_games(date):
    url=f'https://www.nba.com/games?date={date.isoformat()}'
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(request,timeout=30) as response:
        html=response.read().decode('utf-8')
    match=re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
    if not match:
        raise ValueError('NBA 比赛页未包含预期数据；请检查网页结构')
    try:
        page=json.loads(match[1])['props']['pageProps']
        feed=page['gameCardFeed']
        if page.get('hasError') or page['selectedDate']!=date.isoformat() or feed.get('nextPageToken'):
            raise ValueError('日期不匹配、官方页面报错或列表不完整')
        return [card['cardData'] for module in feed['modules'] for card in module['cards']]
    except (KeyError,TypeError,ValueError,AttributeError) as error:
        raise ValueError(f'NBA 比赛页数据无效（{date}）：{error}') from error


def start_time(game):
    moment=dt.datetime.fromisoformat(game['gameTimeUtc'].replace('Z','+00:00'))
    if moment.tzinfo is None:
        raise ValueError('开赛时间缺少时区')
    return moment.astimezone(BEIJING)


def select_games(rows,date,teams=()):
    codes=team_codes(teams)
    selected={}
    try:
        for game in rows:
            identifier=game['gameId']
            if not isinstance(identifier,str) or not re.fullmatch(r'\d{10}',identifier):
                raise ValueError('Game ID 必须为 10 位数字')
            sides={game['awayTeam']['teamTricode'],game['homeTeam']['teamTricode']}
            if start_time(game).date()==date and (not codes or codes & sides):
                selected[identifier]=game
        return sorted(selected.values(),key=start_time)
    except (KeyError,TypeError,ValueError,AttributeError) as error:
        raise ValueError(f'NBA 比赛数据无效：{error}') from error


def list_games(args):
    if args.date:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',args.date):
            raise ValueError('日期格式必须为 YYYY-MM-DD')
        date=dt.date.fromisoformat(args.date)
    else:
        date=today()
    if date==dt.date.min:
        raise ValueError('日期必须晚于 0001-01-01')
    team_codes(args.team)
    # 北京的一天跨两个美国比赛日，按实际开赛时间筛选。
    rows=fetch_games(date-dt.timedelta(days=1))+fetch_games(date)
    selected=select_games(rows,date,args.team)
    print(f'{date}（北京时间） | {len(selected)} 场比赛')
    if not selected:
        print('没有符合条件的比赛')
        return
    print('Game ID     开赛   客队 @ 主队  状态')
    for game in selected:
        away=game['awayTeam']['teamTricode'];home=game['homeTeam']['teamTricode']
        matchup=f'{NAMES.get(away,away)}({away}) @ {NAMES.get(home,home)}({home})'
        status={1:'未开始',2:'进行中',3:'已结束'}.get(game.get('gameStatus'),game.get('gameStatusText') or '未知')
        print(f"{game['gameId']}  {start_time(game):%H:%M}  {matchup}  {status}")
