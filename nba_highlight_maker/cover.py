"""官方球队素材缓存与确定性比赛封面。"""
import datetime as dt
import csv
import json
import os
import re
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nba_highlight_maker import processing as nh
from nba_highlight_maker.players import roster,select_keys,load_selection

NAMES=dict(zip('ATL BOS CLE NOP CHI DAL DEN GSW HOU LAC LAL MIA MIL MIN BKN NYK ORL IND PHI PHX POR SAC SAS OKC TOR UTA MEM WAS DET CHA'.split(),
               '老鹰 凯尔特人 骑士 鹈鹕 公牛 独行侠 掘金 勇士 火箭 快船 湖人 热火 雄鹿 森林狼 篮网 尼克斯 魔术 步行者 76人 太阳 开拓者 国王 马刺 雷霆 猛龙 爵士 灰熊 奇才 活塞 黄蜂'.split()))


def cover_info(info):
    game=info['game'];game_id=info['game_id']
    if not re.fullmatch(r'00[1-6]\d{7}',game_id) or game.get('gameId')!=game_id:
        raise ValueError('封面比赛 ID 不匹配或不支持')
    raw=game.get('gameTimeUTC') or info.get('game_time_utc')
    if not isinstance(raw,str) or not raw:
        raise ValueError('缺少官方开赛时间，无法换算北京时间')
    moment=dt.datetime.fromisoformat(raw.replace('Z','+00:00'))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('官方开赛时间缺少时区，无法换算北京时间')
    date=moment.astimezone(dt.timezone(dt.timedelta(hours=8))).date()
    year=2000+int(game_id[3:5])
    if date.year not in (year,year+1):
        raise ValueError('官方比赛日期与赛季 ID 不一致')
    stages={'Preseason':'季前赛','Regular Season':'常规赛','Playoffs':'季后赛','Play-In':'附加赛','Finals':'总决赛'}
    result=dict(game_id=game_id,date=date.strftime('%Y.%m.%d'),season=f'{year}–{str(year+1)[-2:]}',
                stage=stages.get(game.get('gameLabel'),game.get('gameLabel') or ('常规赛' if game_id.startswith('002') else 'NBA')))
    for side in ['away','home']:
        team=game[side+'Team'];identifier=int(team['teamId'])
        if not 1610612737<=identifier<=1610612766:
            raise ValueError('不支持的 NBA 球队 ID')
        result[side+'_id']=identifier
        result[side+'_name']=NAMES.get(team['teamTricode'],team['teamName'])
        result[side+'_code']=team['teamTricode']
    return result


def cache_logos():
    folder=nh.ROOT/'assets'/'teams';folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/'sources.json'
    if manifest.exists():
        metadata=json.loads(manifest.read_text());urls=metadata['urls']
        if not isinstance(urls,list) or len(set(urls))!=30:
            raise ValueError('Logo 来源缓存不完整')
    else:
        request=urllib.request.Request('https://www.nba.com/teams',headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(request,timeout=30) as response:html=response.read().decode()
        urls=sorted(set(re.findall(r'https://cdn.nba.com/logos/nba/\d+/primary/L/logo.svg',html)))
        if len(urls)!=30:raise ValueError('NBA 球队页面未找到 30 个官方 Logo；请检查网页结构')
        metadata=dict(page='https://www.nba.com/teams',fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),urls=urls)
    def cache(url):
        if not re.fullmatch(r'https://cdn.nba.com/logos/nba/16106127\d{2}/primary/L/logo.svg',url):
            raise ValueError('Logo 来源缓存无效')
        destination=folder/(url.split('/')[5]+'.svg')
        if destination.exists():
            data=destination.read_bytes()
        else:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=30) as response:data=response.read()
        try:
            valid=ET.fromstring(data).tag=='{http://www.w3.org/2000/svg}svg'
        except ET.ParseError as error:
            raise ValueError('球队 Logo 缓存损坏：'+str(destination)) from error
        if not valid:raise ValueError('球队 Logo 不是有效 SVG：'+url)
        if not destination.exists():
            with tempfile.NamedTemporaryFile(dir=folder,delete=False) as file:
                temporary=Path(file.name);file.write(data)
            os.replace(temporary,destination)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(cache,urls))
    nh.atomic_json(manifest,metadata)
    return folder


def team_colors():
    colors=json.loads((nh.ROOT/'assets'/'teams'/'colors.json').read_text())['colors']
    if not isinstance(colors,dict) or set(colors)!=set(NAMES) or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',c) for c in colors.values()):
        raise ValueError('球队颜色缓存无效；需包含 30 队的六位十六进制主色')
    return colors


def publication_text(info,rows=None,periods=None,douyin=False,partial_source=False):
    data=cover_info(info)
    people=roster(info)
    names={key:p['name'] for key,p in people.items()}
    events={}
    for row in rows or []:
        if row['player'] not in names or row['status'] not in ('keep','exclude','pending','missing') or row['kind'] not in ('score','assist','score+assist'):
            raise ValueError('投稿文案遇到无效审核记录')
        if row['status']!='keep':continue
        period,event_id=int(row['period']),int(row['event_id'])
        if period<1 or event_id<0:raise ValueError('投稿文案的节次或事件编号无效')
        events.setdefault((row['player'],period,event_id),set()).update(row['kind'].split('+'))
    players=[p for p in names if any(key[0]==p for key in events)]
    core=set(players)=={'doncic','reaves','kessler'} and all(people[p]['team'] in ('','LAL') for p in players)
    content=('湖人三核' if core else '、'.join(names[p] for p in players) if len(players)<=3 else f'{len(players)}人')+'得分助攻集锦' if players else '比赛集锦'
    scope=sorted(set(periods)) if periods is not None else sorted({key[1] for key in events})
    if any(not isinstance(p,int) or p<1 for p in scope):raise ValueError('投稿文案的录像范围无效')
    title=f"{data['away_name']} VS {data['home_name']}｜{content}｜{data['stage']} {data['date']}"
    if douyin:
        content='湖人三核得分助攻集锦' if core else f'{len(players)}人得分助攻集锦' if players else '比赛集锦'
        title=f"{data['date']} {data['away_name']}vs{data['home_name']} {content}"
        if len(title)>30:
            title=f"{data['date']} {data['away_code']}vs{data['home_code']} {content}"
        if len(title)>30:raise ValueError('抖音标题超过 30 个字符')
    lines=[f"{data['season']} NBA {data['stage']}，{data['away_name']}客场对阵{data['home_name']}。",
           f"比赛日期：{data['date']}"]
    return title,'\n'.join(lines)


def generate_cover(output,review=None,players=None):
    output=Path(output).expanduser().resolve()
    info=json.loads((output/'source.json').read_text())
    data=cover_info(info)
    rows=None;periods=None;partial_source=False
    review_path=Path(review).expanduser().resolve() if review is not None else output/'review.csv'
    if review is not None or review_path.exists():
        with review_path.open(encoding='utf-8-sig',newline='') as file:rows=list(csv.DictReader(file))
    if players is None:players=load_selection(output)
    if players is None and (output/'export.json').exists():
        players=json.loads((output/'export.json').read_text()).get('selected_players')
    if players is not None:
        keys=select_keys(roster(info),players)
        rows=[row for row in rows or [] if row['player'] in keys]
    if (output/'analysis.json').exists():
        analysis=json.loads((output/'analysis.json').read_text())
        if analysis['game_id']!=data['game_id']:raise ValueError('分析缓存比赛 ID 不匹配')
        periods=analysis['quarters']
        partial_source=analysis.get('source_type')=='highlights'
    title,description=publication_text(info,rows,periods,partial_source=partial_source)
    douyin_title,_=publication_text(info,rows,periods,douyin=True,partial_source=partial_source)
    colors=team_colors()
    for side in ['away','home']:
        data[side+'_color']=colors[data[side+'_code']]
    logos=cache_logos()
    source=nh.ROOT/'native'/'cover_render.swift';binary=nh.ROOT/'.cache'/'cover_render'
    if not binary.exists() or binary.stat().st_mtime<source.stat().st_mtime:
        binary.parent.mkdir(exist_ok=True)
        nh.run(['swiftc','-O','-module-cache-path',binary.parent/'swift-cover',source,'-o',binary])
    with tempfile.TemporaryDirectory(prefix='nba-cover-',dir=output) as folder:
        directory=Path(folder);config=directory/'cover.json'
        for width,height,name in [(1920,1080,'cover.png'),(1440,1080,'cover_4x3.png'),(1080,1440,'cover_3x4.png')]:
            config.write_text(json.dumps(dict(data,width=width,height=height,away_logo=str(logos/f"{data['away_id']}.svg"),home_logo=str(logos/f"{data['home_id']}.svg")),ensure_ascii=False))
            nh.run([binary,config,directory/name])
        (directory/'title.txt').write_text(title+'\n',encoding='utf-8')
        (directory/'title_douyin.txt').write_text(douyin_title+'\n',encoding='utf-8')
        (directory/'description.txt').write_text(description+'\n',encoding='utf-8')
        for name in ['cover.png','cover_4x3.png','cover_3x4.png','title.txt','title_douyin.txt','description.txt']:os.replace(directory/name,output/name)
    nh.atomic_json(output/'cover.json',dict(data,width=1920,height=1080,formats={'16:9':dict(file='cover.png',width=1920,height=1080),'4:3':dict(file='cover_4x3.png',width=1440,height=1080),'3:4':dict(file='cover_3x4.png',width=1080,height=1440)},layout='away-left_home-right_diagonal',date_basis='NBA gameTimeUTC converted to Beijing (UTC+08:00)',season_basis='NBA game ID',logos=str(logos)))
    print('已导出封面（16:9、4:3、3:4）及投稿标题、简介：',output,flush=True)
    return output/'cover.png'


def cover(args):
    if not re.fullmatch(r'\d{10}',args.game_id):raise ValueError('比赛 ID 应为 10 位数字')
    generate_cover(nh.ROOT/'output'/args.game_id)
