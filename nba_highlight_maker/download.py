"""Download NBA recordings, verify periods, and archive."""
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse

from nba_highlight_maker import processing as nh


def validate_url(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in {'weibo.com','www.weibo.com','video.weibo.com','m.weibo.cn','weibo.cn','www.weibo.cn','youtube.com','www.youtube.com','m.youtube.com','youtu.be'} or parsed.username or parsed.password:
        raise ValueError('请提供完整的 HTTPS 微博或 YouTube 地址，不要粘贴 Markdown 链接格式')
    return url


def platform(url):
    return 'youtube' if urlparse(url).hostname in {'youtube.com','www.youtube.com','m.youtube.com','youtu.be'} else 'weibo'


def number(text):
    return int(text) if text.isdigit() else {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[text]


def title_periods(title):
    result=set()
    for raw in re.findall(r'第\s*([一二三四五六七八九十\d]+)\s*节',title):
        result.add(number(raw))
    for raw in re.findall(r'\bQ([1-4])\b',title,re.I):
        result.add(int(raw))
    for raw in re.findall(r'(?:第\s*)?([一二三四五六七八九十\d]+)\s*(?:个)?加时',title):
        result.add(4+number(raw))
    for raw in re.findall(r'\bOT([1-9]\d*)\b',title,re.I):
        result.add(4+int(raw))
    return result


def classify(samples,title,source_type='recording'):
    if source_type=='highlights':
        periods=sorted({s['period'] for s in samples if isinstance(s.get('period'),int) and s['period']>0
                        and s.get('clock') is not None and 0<=s['clock']<=(720 if s['period']<=4 else 300)})
        if not periods:
            raise ValueError('集锦没有有效节次与时钟证据；请检查 OCR 配置')
        return dict(name='HIGHLIGHTS.mp4',periods=periods,evidence={},full=False)

    if re.search(r'集锦|精彩片段|highlights',title,re.I):
        raise ValueError('标题标明这是集锦，不能作为完整节次录像自动归档')
    unresolved_ot=sum(s.get('period') is None and s.get('clock') is not None and
                      s.get('raw',{}).get('period','').strip().upper()=='OT' for s in samples)
    if unresolved_ot>=3:
        raise ValueError('检测到加时，但官方比分不足以唯一确定加时编号；需要人工确认')
    valid=[s for s in samples if isinstance(s.get('period'),int) and s['period']>0 and s.get('clock') is not None
           and 0<=s['clock']<=(720 if s['period']<=4 else 300)]
    counts=Counter(s['period'] for s in valid)
    periods=[]
    evidence={}
    for p,count in sorted(counts.items()):
        group=[s for s in valid if s['period']==p]
        moving=sum(0<b['t']-a['t']<=90 and 0<a['clock']-b['clock']<=b['t']-a['t']+1
                   for a,b in zip(group,group[1:]))
        if count>=3 and moving>=2:
            periods.append(p)
            evidence[str(p)]=dict(samples=count,countdown_transitions=moving,first_t=group[0]['t'],last_t=group[-1]['t'])
    if not periods:
        raise ValueError('没有足够的节次与连续比赛时钟证据；保留下载文件，不猜测节次')
    order=[s['period'] for s in valid if s['period'] in periods]
    if any(b<a for a,b in zip(order,order[1:])):
        raise ValueError('节次倒退，可能是回放或拼接集锦；需要人工确认')
    hints=title_periods(title)
    if hints and hints!=set(periods):
        raise ValueError(f'标题节次 {sorted(hints)} 与画面识别 {periods} 冲突')
    full=set(range(1,5)).issubset(periods)
    if not hints and re.search(r'全场|完整比赛|full\s*game',title,re.I) and not full:
        raise ValueError('标题称全场，但画面未确认全部四节；需要人工确认')
    if full:
        name='FULL.mp4'
    elif len(periods)==1:
        name=nh.period_name(periods[0])+'.mp4'
    else:
        name='-'.join(nh.period_name(p) for p in periods)+'.mp4'
    return dict(name=name,periods=periods,evidence=evidence,full=full)


def archive(source,target):
    # Hard-link creation is atomic and fails if the destination already exists.
    if target.exists() and source.samefile(target):
        return
    os.link(source,target)


def invoke_ytdlp(arguments,progress=False):
    command=['yt-dlp','--ignore-config','--no-playlist',*map(str,arguments)]
    if progress:
        # Inherit the terminal so yt-dlp controls carriage returns and ANSI colors.
        command[1:1]=['--progress','--progress-delta','1']
        process=subprocess.run(command)
        if process.returncode:
            raise RuntimeError(f'yt-dlp 失败（状态码 {process.returncode}）；详细原因见上方终端输出')
        return ''
    process=subprocess.run(command,capture_output=True,text=True)
    if process.returncode:
        raise RuntimeError('yt-dlp 失败：'+process.stderr[-2000:])
    return process.stdout


def download_media(url,stage):
    completed=stage/'video.mp4'
    if completed.exists():
        nh.probe(completed)
        print('复用已完成的下载',flush=True)
        return completed
    print('下载最高可用画质；中断后的 .part 文件会保留供重试',flush=True)
    invoke_ytdlp(['--no-overwrites','--continue','-f','bv*+ba/b','--merge-output-format','mp4',
                  '--remux-video','mp4','-o',stage/'video.%(ext)s',url],progress=True)
    if not completed.is_file():
        raise ValueError('下载结束但没有 MP4 文件；请检查下载目录')
    nh.probe(completed)
    return completed


def identify_video(path,stage,pbp,title,source_type='recording',ocr_profile='espn'):
    signature=dict(source_type=source_type,ocr_profile=ocr_profile,size=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns,
                   ocr=hashlib.sha256((nh.ROOT/'native'/'vision_ocr.swift').read_bytes()).hexdigest())
    cache=stage/'identification.json'
    if cache.exists():
        old=json.loads(cache.read_text())
        if old.get('signature')==signature:
            result=classify(nh.resolve_overtime(old['samples'],pbp),title,source_type)
            if not set(result['periods']).issubset({a['period'] for a in pbp['actions']}):
                raise ValueError('录像节次不在指定比赛官方数据中，可能选错比赛')
            return result
    step=2 if source_type=='highlights' else 20
    duration=nh.probe(path)
    binary=nh.vision_binary()
    with tempfile.TemporaryDirectory(prefix='nba-identify-') as folder:
        root=Path(folder)
        check=root/'check.jpg'
        nh.frame(path,min(120,duration/2),check)
        nh.vision([check],binary,ocr_profile=ocr_profile)
        nh.run(['ffmpeg','-v','error','-i',path,'-vf',f'fps=1/{step}:start_time=0,scale=1280:-2',
                '-q:v','2','-y',root/'%06d.jpg'])
        paths=sorted(root.glob('[0-9]*.jpg'))
        samples=[]
        for i in range(0,len(paths),100):
            samples.extend(nh.parse_ocr(x,(i+j)*step) for j,x in enumerate(nh.vision(paths[i:i+100],binary,ocr_profile=ocr_profile)))
            print(f'节次识别：{min(i+100,len(paths))}/{len(paths)} 帧',flush=True)
    nh.atomic_json(cache,dict(signature=signature,samples=samples))
    result=classify(nh.resolve_overtime(samples,pbp),title,source_type)
    official={a['period'] for a in pbp['actions']}
    if not set(result['periods']).issubset(official):
        raise ValueError('录像节次不在指定比赛官方数据中，可能选错比赛')
    return result


def context(args):
    game_id=args.game_id
    if game_id is None:
        cached=list((nh.ROOT/'output').glob('*/source.json'))
        if len(cached)!=1:
            raise ValueError('无法唯一确定比赛；请增加 --game-id NBA比赛ID')
        game_id=cached[0].parent.name
        print('使用当前唯一缓存比赛：'+game_id,flush=True)
    if not re.fullmatch(r'\d{10}',game_id):
        raise ValueError('Game ID 必须为 10 位数字')
    pbp,info=nh.load_game(nh.ROOT/'output'/game_id,game_id,args.refresh_data)
    if args.game_dir:
        game_dir=Path(args.game_dir).expanduser().resolve()
    else:
        game_date=info['game']['gameEt'][:10]
        game_dir=Path.home()/'Movies'/'NBA'/'games'/f"{game_date}_{info['home']}_vs_{info['away']}"
    args.game_id=game_id
    return game_dir,pbp,info


def download_all(args):
    source_type=getattr(args,'source_type','recording')
    ocr_profile=getattr(args,'ocr_profile','espn')
    for url in args.urls:
        validate_url(url)
    game_dir,pbp,info=context(args)
    game_dir.mkdir(parents=True,exist_ok=True)
    manifest_path=game_dir/'recordings.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else dict(game_id=args.game_id,recordings=[])
    if manifest.get('game_id')!=args.game_id:
        raise ValueError('该目录属于另一场比赛，拒绝混入录像')
    print(f"归档比赛：{info['away']} @ {info['home']}；目录：{game_dir}",flush=True)
    for index,url in enumerate(args.urls,1):
        print(f'[{index}/{len(args.urls)}] 处理：{url}',flush=True)
        prior=next((r for r in manifest['recordings'] if r.get('url')==url),None)
        if prior:
            if (prior.get('source_type','recording'),prior.get('ocr_profile','espn'))!=(source_type,ocr_profile):
                raise ValueError('该视频已归档，但类型或 OCR 配置不同；请核查 recordings.json，不能静默复用旧校准')
            path=game_dir/prior['file']
            if not path.is_file():
                raise ValueError('归档记录存在，但录像丢失：'+str(path))
            nh.probe(path)
            print('该地址已归档，跳过重复下载：'+str(path),flush=True)
            continue
        stage=game_dir/'.downloads'/hashlib.sha256(url.encode()).hexdigest()[:16]
        stage.mkdir(parents=True,exist_ok=True)
        metadata_path=stage/'metadata.json'
        if metadata_path.exists():
            metadata=json.loads(metadata_path.read_text())
        else:
            raw=json.loads(invoke_ytdlp(['--dump-single-json','--skip-download',url]))
            if raw.get('_type')=='playlist':
                raise ValueError('一次地址必须对应一个录像文件')
            metadata={k:raw.get(k) for k in ['id','title','description','duration','timestamp','upload_date','webpage_url']}
            nh.atomic_json(metadata_path,metadata)
        prior=next((r for r in manifest['recordings'] if metadata.get('id') and r.get('video_id')==metadata['id'] and r.get('platform',platform(r['url']))==platform(url)),None)
        if prior:
            if (prior.get('source_type','recording'),prior.get('ocr_profile','espn'))!=(source_type,ocr_profile):
                raise ValueError('相同平台视频已归档，但类型或 OCR 配置不同；请核查 recordings.json')
            nh.probe(game_dir/prior['file'])
            print('相同平台视频已归档，跳过别名地址：'+prior['file'],flush=True)
            continue
        source=stage/'video.mp4'
        try:
            source=download_media(url,stage)
            title=(metadata.get('title') or '')+' '+(metadata.get('description') or '')
            result=identify_video(source,stage,pbp,title,source_type,ocr_profile)
            if source_type=='highlights':
                result['name']='HL-'+hashlib.sha256(url.encode()).hexdigest()[:16]+'.mp4'
            official={a['period'] for a in pbp['actions']}
            if result['full'] and not official.issubset(result['periods']):
                raise ValueError('画面未确认该场比赛的全部加时，不能命名为全场录像')
            target=game_dir/result['name']
            if target.exists() and not source.samefile(target):
                target=target.with_name(target.stem+'-'+hashlib.sha256(url.encode()).hexdigest()[:16]+target.suffix)
                if target.exists() and not source.samefile(target):
                    raise FileExistsError('补充源目标已存在，保留两份文件并停止：'+str(target))
                result['name']=target.name
                print('该节已有录像，新增补充源：'+target.name,flush=True)
            record=dict(source_type=source_type,ocr_profile=ocr_profile,platform=platform(url),url=url,video_id=metadata.get('id'),file=target.name,periods=result['periods'],
                        classification=result,metadata=metadata,archived_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        size=source.stat().st_size)
            archive(source,target)
            manifest['recordings'].append(record)
            nh.atomic_json(manifest_path,manifest)
            source.unlink()
            print('已归档：'+str(target),flush=True)
        except (OSError,ValueError,RuntimeError) as error:
            nh.atomic_json(stage/'error.json',dict(url=url,error=str(error),retained_download=str(source)))
            raise RuntimeError(f'{error}\n已保留下载与识别记录：{stage}') from error
    return game_dir


