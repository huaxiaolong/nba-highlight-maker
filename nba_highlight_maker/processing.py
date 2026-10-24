"""Official NBA data, Vision alignment, review files, and highlight export."""
import csv
from collections import Counter
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from nba_highlight_maker.players import LEGACY_PLAYERS as PLAYERS, LEGACY_NAMES, roster, load_roster, load_selection, select_keys

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["event_id", "player", "kind", "period", "clock", "description", "source",
          "start", "end", "status", "reason", "candidates", "preview", "source_candidates"]


class Progress:
    def __init__(self,label,total,unit="段"):
        self.label,self.total,self.unit=label,total,unit
        self.started=time.monotonic()
        self.last=-1
        self.finished=False
        self.update(0)

    def update(self,done):
        now=time.monotonic()
        done=min(max(done,0),self.total)
        complete=done==self.total
        if self.finished or (self.last>=0 and now-self.last<1 and not complete):
            return
        self.last=now
        elapsed=now-self.started
        rate=done/elapsed if elapsed>0 else 0
        eta=(self.total-done)/rate if rate else None
        remaining=f"{int(eta)//60:02d}:{int(eta)%60:02d}" if eta is not None else "--:--"
        percent=100*done/self.total if self.total else 100
        text=f"[{self.label}] {percent:5.1f}% {done:g}/{self.total:g} {self.unit} {rate:.1f} {self.unit}/s ETA {remaining}"
        tty=sys.stderr.isatty()
        if tty:
            text="\r\033[2K\033[36m"+text+"\033[0m"
        print(text,file=sys.stderr,end="\n" if complete or not tty else "",flush=True)
        self.finished=complete

    def log(self,text):
        if sys.stderr.isatty() and not self.finished:
            print("\r\033[2K",file=sys.stderr,end="",flush=True)
        print(text,file=sys.stderr,flush=True)


def stream_run(command,on_line,stdin=None):
    with tempfile.TemporaryFile(mode="w+t") as errors:
        with subprocess.Popen([str(x) for x in command],stdin=stdin,stdout=subprocess.PIPE,stderr=errors,text=True,bufsize=1) as process:
            try:
                for line in process.stdout:
                    on_line(line)
            except BaseException:
                process.terminate()
                print(file=sys.stderr,flush=True)
                raise
            if process.wait():
                errors.seek(0)
                print(file=sys.stderr,flush=True)
                raise RuntimeError(f"命令失败 ({command[0]}):\n{errors.read()[-4000:]}")


def format_video_time(seconds):
    milliseconds=round(float(seconds)*1000)
    seconds,milliseconds=divmod(milliseconds,1000)
    minutes,seconds=divmod(seconds,60)
    hours,minutes=divmod(minutes,60)
    return (f"{hours:02d}:" if hours else "")+f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def parse_video_time(raw):
    text=str(raw).strip()
    if ':' in text:
        match=re.fullmatch(r'(?:(\d+):)?(\d+):(\d{1,2}(?:\.\d+)?)',text)
        if not match:
            raise ValueError('录像时间应为 分:秒、时:分:秒 或原有秒数：'+text)
        hours,minutes,seconds=match.groups()
        if float(seconds)>=60 or (hours is not None and int(minutes)>=60):
            raise ValueError('录像时间的秒必须小于60，时分秒格式的分也必须小于60：'+text)
        value=int(hours or 0)*3600+int(minutes)*60+float(seconds)
    else:
        value=float(text)
    if not math.isfinite(value) or value<0:
        raise ValueError('录像时间必须是有限的非负数：'+text)
    return value


def describe_clip(row,index,total,names=None):
    player=(names or LEGACY_NAMES).get(row['player'],row['player'])
    kind={'score':'运动战得分','assist':'助攻','score+assist':'得分/助攻'}[row['kind']]
    status='自动候选' if row['status']=='keep' else '待确认'
    heading=f"[{index}/{total}] {player} | {kind} | {period_name(int(row['period']))} | 比赛时钟 {format_video_time(parse_clock(row['clock']))} | {status}"
    if row['start']=='' or row['end']=='':
        times='源录像起止：未定位；未生成预览'
    else:
        start,end=parse_video_time(row['start']),parse_video_time(row['end'])
        times=f"源录像：{Path(row['source']).name} {format_video_time(start)} → {format_video_time(end)}（{start:.3f}—{end:.3f} 秒，时长 {end-start:.3f} 秒）"
    detail=f"\n  {row['description']}\n  {times}"
    if row['preview']:
        detail+=f"\n  预览：{row['preview']}"
    if row['status']=='pending':
        detail+=f"\n  原因：{row['reason']}；候选发生秒数：{row['candidates'] or '无'}"
    return heading+detail


def parse_clock(raw):
    text = str(raw).strip()
    match = re.fullmatch(r"PT(\d+)M(\d+(?:\.\d+)?)S", text)
    if not match:
        match = re.fullmatch(r"(\d{1,2}):(\d{2}(?:\.\d+)?)", text)
    if match:
        minutes, seconds = map(float, match.groups())
        if seconds >= 60:
            raise ValueError("Invalid seconds")
        value = minutes * 60 + seconds
    elif re.fullmatch(r"\d{1,2}\.\d+", text):
        value = float(text)
        if value >= 60:
            raise ValueError("Invalid last-minute clock")
    else:
        raise ValueError("Invalid game clock: " + text)
    if not 0 <= value <= 720:
        raise ValueError("Clock outside regulation quarter")
    return value


def ascii_name(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def select_events(pbp, periods, players=None):
    players=PLAYERS if players is None else players
    last_names=Counter(ascii_name(name).lower() for _,name in players.values())
    result, seen = [], set()
    for index, action in enumerate(pbp["actions"]):
        if action.get("period") not in periods or action.get("isFieldGoal") != 1 or action.get("shotResult") != "Made":
            continue
        for player, (person, name) in players.items():
            scoring = action.get("personId") == person
            assisting = action.get("assistPersonId") == person
            if action.get('assistPersonId') is None and name and last_names[ascii_name(name).lower()]==1:
                assisting=bool(re.search(r"\(" + re.escape(ascii_name(name)) + r" \d+ AST\)",ascii_name(action.get('description','')),re.I))
            if not (scoring or assisting):
                continue
            identifier = str(action.get("actionId", index))
            key = (player, action["period"], identifier)
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(action, event_id=identifier, player=player,
                               kind="score+assist" if scoring and assisting else "score" if scoring else "assist"))
    return sorted(result, key=lambda e: (e["period"], -parse_clock(e["clock"]), int(e["event_id"])))


def summarize_events(events,players=None):
    return {player:dict(scores=sum('score' in e['kind'] for e in events if e['player']==player),
                        assists=sum('assist' in e['kind'] for e in events if e['player']==player),
                        clips=sum(e['player']==player for e in events)) for player in (PLAYERS if players is None else players)}


def locate_event(event, samples):
    target = parse_clock(event["clock"])
    valid = [s for s in samples if s.get("clock") is not None and s.get("period") == event["period"]]
    candidates = []
    expected = (int(event["scoreHome"]), int(event["scoreAway"]))
    for i in range(1, len(valid)):
        left, right = valid[i-1], valid[i]
        elapsed = right["t"] - left["t"]
        drop = left["clock"] - right["clock"]
        if not (0 < elapsed <= 2.6 and 0 < drop <= elapsed + 0.8 and left["clock"] >= target >= right["clock"]):
            continue
        t = left["t"] + (left["clock"] - target) / drop * elapsed
        neighbors = [s for s in valid if abs(s["t"] - t) <= 4]
        transitions = sum(0 < b["t"]-a["t"] <= 2.6 and
                          0 < a["clock"]-b["clock"] <= b["t"]-a["t"]+0.8
                          for a, b in zip(neighbors, neighbors[1:]))
        if transitions < 2:
            continue
        # The ESPN goal animation can hide the new score for several seconds.
        scores = [(s["home"], s["away"]) for s in valid
                  if t+1 <= s["t"] <= t+8 and s.get("home") is not None and s.get("away") is not None]
        if scores and expected not in scores:
            continue
        if not candidates or t - candidates[-1] > 2.5:
            candidates.append(round(t, 3))
    if len(candidates) == 1:
        return dict(candidates=candidates, status="keep", reason="唯一连续时钟候选；请观看确认动作完整")
    return dict(candidates=candidates, status="pending", reason="重复时钟或回放：需要人工确认" if candidates else "缺少可靠时钟/比分证据：需要人工定位")


def merge_ranges(rows):
    merged = []
    for source, start, end in rows:
        if merged and merged[-1][0] == source and start <= merged[-1][2]:
            previous = merged[-1]
            merged[-1] = (source, previous[1], max(previous[2], end))
        else:
            merged.append((source, start, end))
    return merged


def clip_bounds(t, duration):
    return round(max(0, t-8), 3), round(min(duration, t+3), 3)


def validate_range(start, end, duration):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration):
        raise ValueError(f"片段范围无效：{start}—{end}，录像长度 {duration}")


def validate_review(rows):
    for row in rows:
        if row["status"]=="missing" and not row.get("reason","").strip():
            raise ValueError("missing 必须记录人工确认依据")
        if row["status"] not in ("keep", "exclude", "missing"):
            raise ValueError("仍有 pending 事件。请修改时间并设为 keep，或人工确认后设为 missing / exclude。")


def preserve_review(existing, fresh):
    keys = {(r["player"],str(r["period"]),r["event_id"]) for r in existing}
    return existing + [r for r in fresh if (r["player"],str(r["period"]),r["event_id"]) not in keys]


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def validate_pbp(data, game_id):
    if not isinstance(data,dict) or data.get("gameId") != game_id or not isinstance(data.get("actions"), list) or not data["actions"]:
        raise ValueError("比赛数据无效或 ID 不匹配；使用 --refresh-data 刷新")
    for action in data["actions"]:
        if not isinstance(action, dict) or not isinstance(action.get("period"), int) or "clock" not in action:
            raise ValueError("逐回合数据缺少必需字段；使用 --refresh-data 刷新")


def fetch_game(game_id):
    url = f"https://www.nba.com/game/{game_id}/play-by-play"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8")
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("NBA 网页未包含预期数据；请检查网页结构")
    page = json.loads(match.group(1))["props"]["pageProps"]
    game = page["game"]
    info = dict(game_id=game_id, url=url, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                home=game["homeTeam"]["teamTricode"], away=game["awayTeam"]["teamTricode"],
                game_time_utc=game.get("gameTimeUTC"), game_status=game.get("gameStatus"),
                game=game)
    return page["playByPlay"], info


def load_game(output, game_id, refresh=False):
    pbp_path, info_path = output / "play_by_play.json", output / "source.json"
    if not refresh and (pbp_path.exists() or info_path.exists()):
        try:
            pbp = json.loads(pbp_path.read_text())
            info = json.loads(info_path.read_text())
            validate_pbp(pbp, game_id)
            if not isinstance(info,dict) or info.get("game_id") != game_id:
                raise ValueError("来源 ID 不匹配")
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("缓存无效；使用 --refresh-data 刷新") from error
        print("复用官方数据缓存", flush=True)
        return pbp, info
    pbp, info = fetch_game(game_id)
    validate_pbp(pbp, game_id)
    if not isinstance(info,dict) or info.get("game_id") != game_id:
        raise ValueError("来源 ID 不匹配")
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nba-data-",dir=output) as folder:
        staging=Path(folder)
        atomic_json(staging/"source.json",info)
        atomic_json(staging/"play_by_play.json",pbp)
        os.replace(staging/"play_by_play.json",pbp_path)
        os.replace(staging/"source.json",info_path)
    print("已保存完整官方数据", flush=True)
    return pbp, info


def run(command,progress=None):
    if progress is not None:
        def update(line):
            if line.startswith('out_time_us='):
                value=line.strip().split('=',1)[1]
                if value.isdigit():
                    progress.update(int(value)/1_000_000)
        stream_run([command[0],'-progress','pipe:1','-nostats',*command[1:]],update)
        progress.update(progress.total)
        return ''
    process = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(f"命令失败 ({command[0]}):\n{process.stderr[-4000:]}")
    return process.stdout


def probe(path,require_audio=True):
    data = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path]))
    if not any(s["codec_type"] == "video" for s in data["streams"]):
        raise ValueError("录像没有视频流：" + str(path))
    if require_audio and not any(s["codec_type"] == "audio" for s in data["streams"]):
        raise ValueError("录像没有音频流，无法保留原解说：" + str(path))
    return float(data["format"]["duration"])


def vision_binary():
    source = ROOT / "native" / "vision_ocr.swift"
    binary = ROOT / ".cache" / "vision_ocr"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        binary.parent.mkdir(exist_ok=True)
        run(["swiftc", "-O", "-module-cache-path", binary.parent / "swift-modules", source, "-o", binary])
    return binary


def vision(paths, binary,progress=None,ocr_profile="espn"):
    if ocr_profile not in ("espn","spectrum"):
        raise ValueError("未知 OCR 配置")
    command=[str(binary),ocr_profile]
    if progress is not None:
        results=[]
        def update(line):
            if line.strip():
                result=json.loads(line)
                check_vision_result(result)
                results.append(result)
                progress.update(len(results))
        with tempfile.TemporaryFile(mode='w+t') as input_file:
            input_file.write(''.join(str(p)+'\n' for p in paths));input_file.seek(0)
            stream_run(command,update,stdin=input_file)
    else:
        process = subprocess.run(command, input="".join(str(p)+"\n" for p in paths), capture_output=True, text=True)
        if process.returncode:
            raise RuntimeError("Vision 程序失败：" + process.stderr[-2000:])
        results = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    if len(results) != len(paths):
        raise RuntimeError("Vision 返回数量不匹配")
    for result in results:
        check_vision_result(result)
    return results


def check_vision_result(result):
    if 'error' in result:
        raise RuntimeError("Apple Vision 能力检查失败：" + result["error"] +
                           "。本环境已确认沙盒会限制 IOSurface；请在正常终端运行，或授权沙盒外执行。")


def parse_ocr(result, timestamp):
    texts = {name: " ".join(x["text"] for x in result.get(name, [])) for name in ["clock", "period", "home", "away"]}
    try:
        clock = parse_clock(texts["clock"].replace(" ", ""))
    except ValueError:
        clock = None
    label=texts["period"].upper().strip()
    overtime=re.search(r"(?:(\d+)\s*OT|OT\s*(\d+))",label)
    period = re.search(r"([1-4I])\s*(?:ST|ND|RD|TH)", label)
    if overtime:
        period=4+int(overtime.group(1) or overtime.group(2))
    elif re.fullmatch(r"OT",label):
        period=5
    else:
        period = int(period.group(1).replace("I", "1")) if period else None
    values = {}
    for field in ["home", "away"]:
        numbers = re.findall(r"(?<!\w)\d{1,3}(?!\w)", texts[field])
        values[field] = int(numbers[-1]) if len(numbers) == 1 else None
    return dict(t=round(timestamp, 3), clock=clock, period=period, raw=texts, **values)


def resolve_overtime(samples,pbp):
    ranges={}
    previous=None
    for action in pbp['actions']:
        p=action['period']
        if p>4 and p not in ranges and previous is not None:
            ranges[p]=[previous[0],previous[0],previous[1],previous[1]]
        if action.get('scoreHome') and action.get('scoreAway'):
            home,away=int(action['scoreHome']),int(action['scoreAway'])
            previous=(home,away)
            if p>4:
                bounds=ranges.setdefault(p,[home,home,away,away])
                bounds[:]=[min(bounds[0],home),max(bounds[1],home),min(bounds[2],away),max(bounds[3],away)]
    resolved=[]
    for sample in samples:
        current=dict(sample)
        if sample.get('raw',{}).get('period','').strip().upper()=='OT':
            home,away=sample.get('home'),sample.get('away')
            options=[p for p,b in ranges.items() if home is not None and away is not None and b[0]<=home<=b[1] and b[2]<=away<=b[3]]
            if len(ranges)==1 and (home is None or away is None):
                options=list(ranges)
            current['period']=options[0] if len(options)==1 else None
        resolved.append(current)
    return resolved


def period_name(period):
    return f'Q{period}' if period<=4 else f'OT{period-4}'


def discover_videos(game_dir,game_id):
    videos={}
    for path in sorted(game_dir.glob('*.mp4')):
        match=re.fullmatch(r'Q([1-4])|OT([1-9]\d*)',path.stem)
        if match:
            p=int(match.group(1)) if match.group(1) else 4+int(match.group(2))
            videos[p]=path.resolve()
    manifest=game_dir/'recordings.json'
    if manifest.exists():
        records=json.loads(manifest.read_text())
        if records.get('game_id')!=game_id:
            raise ValueError('录像归档的比赛 ID 与指定比赛不匹配')
        for record in sorted(records['recordings'],key=lambda r:len(r['periods'])):
            path=(game_dir/record['file']).resolve()
            if path.parent!=game_dir.resolve() or not path.is_file():
                raise ValueError('录像归档文件缺失或路径无效：'+str(path))
            for p in record['periods']:
                if not isinstance(p,int) or p<1:
                    raise ValueError('录像归档节次无效')
                videos.setdefault(p,path)
    return dict(sorted(videos.items()))


def discover_sources(game_dir,game_id):
    videos=discover_videos(game_dir,game_id)
    manifest=game_dir/'recordings.json'
    records=json.loads(manifest.read_text())['recordings'] if manifest.exists() else []
    sources={}
    for period,path in videos.items():
        sources[period]=[dict(path=path,source_type='recording',ocr_profile='espn')]
    for record in records:
        path=(game_dir/record['file']).resolve()
        source_type=record.get('source_type','recording')
        profile=record.get('ocr_profile','espn')
        if source_type not in ('recording','highlights') or profile not in ('espn','spectrum'):
            raise ValueError('录像归档类型或 OCR 配置无效')
        for period in record['periods']:
            group=sources.setdefault(period,[])
            group[:]=[item for item in group if item['path']!=path]
            group.append(dict(path=path,source_type=source_type,ocr_profile=profile))
    return dict(sorted(sources.items()))


def locate_sources(event,paths,scanned):
    options=[]
    results={path:locate_event(event,scanned[path]) for path in paths}
    for path,result in results.items():
        options.extend(dict(source=str(path),t=t) for t in result['candidates'])
    path=Path(options[0]['source']) if options else paths[0]
    located=dict(results[path],source=path,source_candidates=options)
    if len(options)>1:
        located.update(status='pending',reason='多个源录像或回放候选：需要人工选择源录像与区间')
    return located


def frame(path, timestamp, destination):
    run(["ffmpeg", "-v", "error", "-ss", timestamp, "-i", path, "-frames:v", "1",
         "-vf", "scale=1280:-2", "-y", destination])


def scan_video(path, output, events, binary, duration,ocr_profile="espn"):
    targets_signature=[list(item) for item in sorted({(e["period"],e["clock"]) for e in events})]
    signature = dict(ocr_profile=ocr_profile,targets=targets_signature,path=str(path), size=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
                     ocr=hashlib.sha256((ROOT / "native" / "vision_ocr.swift").read_bytes()).hexdigest())
    cache = output / f"ocr_{path.stem}.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get("signature") == signature:
            print(f"{path.name}: 复用 OCR 缓存", flush=True)
            return saved["samples"]
    with tempfile.TemporaryDirectory(prefix="nba-frames-") as folder:
        directory = Path(folder)
        first = directory / "check.jpg"
        frame(path, min(120, duration/2), first)
        vision([first], binary,ocr_profile=ocr_profile)
        print(f"{path.name}: 正在建立录像时间轴（抽帧）", flush=True)
        run(["ffmpeg", "-v", "error", "-i", path, "-vf", "fps=1/2:start_time=0,scale=1280:-2",
             "-q:v", "2", "-y", directory / "%06d.jpg"],progress=Progress(f"{path.name} 时间轴抽样",duration,'秒'))
        paths = sorted(directory.glob("[0-9]*.jpg"))
        samples = []
        print(f"{path.name}: 正在识别比赛时钟",flush=True)
        samples.extend(parse_ocr(x,i*2) for i,x in enumerate(vision(paths,binary,progress=Progress(f"{path.name} 时钟识别",len(paths),'采样点'),ocr_profile=ocr_profile)))
        targets = [parse_clock(e["clock"]) for e in events]
        windows = []
        for sample in samples:
            if sample["clock"] is not None and any(abs(sample["clock"]-target) <= 3 for target in targets):
                windows.append((str(path), max(0,sample["t"]-4), min(duration-0.1,sample["t"]+4)))
        dense_times = set()
        for _, start, end in merge_ranges(windows):
            dense_times.update(i/2 for i in range(math.ceil(start*2), math.floor(end*2)+1))
        dense_times -= {s["t"] for s in samples}
        dense_paths = []
        print(f"{path.name}: 正在精确定位候选回合", flush=True)
        dense_progress=Progress(f"{path.name} 回合精确采样",len(dense_times),'采样点')
        for i, t in enumerate(sorted(dense_times)):
            destination = directory / f"dense_{i:06d}.jpg"
            frame(path, t, destination)
            dense_paths.append(destination)
            dense_progress.update(i+1)
        if dense_paths:
            samples.extend(parse_ocr(x,t) for x,t in zip(vision(dense_paths,binary,progress=Progress(f"{path.name} 精确时钟识别",len(dense_paths),'采样点'),ocr_profile=ocr_profile),sorted(dense_times)))
    samples.sort(key=lambda s:s["t"])
    atomic_json(cache, dict(signature=signature, samples=samples))
    return samples


def render_clip(source, start, end, destination,progress=None,watermark=False,allow_silent=False):
    temporary = destination.with_name(destination.stem + ".tmp.mp4")
    command=["ffmpeg", "-v", "error", "-ss", start, "-i", source]
    audio='0:a:0'
    if allow_silent and not run(['ffprobe','-v','error','-select_streams','a','-show_entries','stream=index','-of','csv=p=0',source]).strip():
        command.extend(['-f','lavfi','-i','anullsrc=r=48000:cl=stereo'])
        audio='1:a:0'
    scale="scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    if watermark:
        logo=ROOT/'assets'/'nba2k-logo.png'
        if not logo.is_file():
            raise ValueError("缺少正片 Logo 素材："+str(logo))
        command.extend(["-i",logo,"-filter_complex",
                        f"[0:v:0]{scale}[base];[1:v:0]scale=300:-1[logo];[base][logo]overlay=x=W-w-32:y=32[v]",
                        "-map","[v]"])
    else:
        command.extend(["-vf",scale,"-map","0:v:0"])
    run([*command,"-t",round(end-start,3),"-map", audio,
         "-r", "25", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-y", temporary],progress=progress)
    os.replace(temporary, destination)


def analyze(args):
    started = time.monotonic()
    if not re.fullmatch(r"\d{10}", args.game_id):
        raise ValueError("Game ID 必须为 10 位数字")
    game_dir = Path(args.game_dir).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve() if getattr(args,"output_dir",None) else ROOT / "output" / args.game_id
    output.mkdir(parents=True, exist_ok=True)
    sources = discover_sources(game_dir,args.game_id)
    videos={p:group[0]["path"] for p,group in sources.items()}
    if not videos:
        raise ValueError("比赛目录中没有 Q1—Q4、OT1 等录像或有效归档记录")
    pbp, info = load_game(output,args.game_id,args.refresh_data)
    if {info["home"],info["away"]} != {"LAL","SAC"}:
        print("注意：当前 OCR 区域只校准过 ESPN 湖人对国王录像；请核对球队顺序",flush=True)
    print(f"官方比赛：{info['away']} @ {info['home']}，录像节次：{list(videos)}",flush=True)
    highlight_mode=any(item['source_type']=='highlights' for group in sources.values() for item in group)
    people=roster(info)
    requested=getattr(args,'players',None)
    keys=select_keys(people,load_selection(output) if requested is None else requested)
    players={key:(people[key]['id'],people[key]['last_name']) for key in keys}
    names={key:p['name'] for key,p in people.items()}
    events = select_events(pbp,{a['period'] for a in pbp['actions']} if highlight_mode else set(videos),players)
    summary=summarize_events(events,players)
    print(f"输入录像：{len({item['path'] for group in sources.values() for item in group})} 份；节次：{'、'.join(period_name(p) for p in videos)}",flush=True)
    for player in players:
        name=names[player]
        counts=summary[player]
        print(f"{name}：运动战进球 {counts['scores']} 次，助攻 {counts['assists']} 次，候选片段 {counts['clips']} 段",flush=True)
    print(f"共 {len(events)} 段球员候选；重叠区间可能合并，最终片段数以审核导出为准。",flush=True)
    atomic_json(output/"events.json",events)
    binary = vision_binary()
    rows, scanned, durations = [], {}, {}
    timings=[]
    file_settings={item['path']:item for group in sources.values() for item in group}
    files=list(file_settings)
    input_signature=[dict(path=str(path),size=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns,
                          source_type=item['source_type'],ocr_profile=item['ocr_profile']) for path,item in sorted(file_settings.items())]
    old_analysis=json.loads((output/'analysis.json').read_text()) if (output/'analysis.json').exists() else {}
    sources_changed=old_analysis.get('input_signature')!=input_signature
    for index,path in enumerate(files,1):
        print(f"分析录像 [{index}/{len(files)}] {path.name}",flush=True)
        scan_started=time.monotonic()
        duration=probe(path)
        file_events=[e for e in events if any(item['path']==path for item in sources.get(e['period'],[]))]
        profile=file_settings[path]['ocr_profile']
        samples=resolve_overtime(scan_video(path,output,file_events,binary,duration,ocr_profile=profile),pbp) if file_events else []
        elapsed=time.monotonic()-scan_started
        if not file_events:
            print(f"{path.name}: 无目标球员回合，跳过时间轴识别",flush=True)
        print(f"{path.name}: 时间轴准备完成，耗时 {elapsed:.1f} 秒；目标候选 {len(file_events)} 段",flush=True)
        timings.append(dict(file=path.name,candidates=len(file_events),seconds=round(elapsed,2)))
        durations[path]=duration
        scanned[path]=samples
    preview_dir = output / "previews"
    preview_dir.mkdir(exist_ok=True)
    previous = {}
    previews_progress=Progress("候选片段处理",len(events))
    for index,event in enumerate(events,1):
        quarter=event["period"]
        paths=[item['path'] for item in sources.get(quarter,[])]
        located=locate_sources(event,paths,scanned) if paths else dict(source=None,candidates=[],source_candidates=[],status='pending',reason='提供的视频没有该节证据：待人工确认')
        source=located['source']
        candidates=located['candidates']
        t=candidates[0] if candidates else None
        key=(quarter,source)
        if t is not None and t < previous.get(key,0):
            located.update(status="pending",reason="事件顺序冲突：需要人工确认")
        if t is not None and located["status"]=="keep":
            previous[key]=t
        if highlight_mode:
            located.update(status='pending',reason=located['reason']+'；集锦需确认动作完整及剪辑边界')
        start,end=clip_bounds(t,durations[source]) if t is not None else ("","")
        preview=""
        if t is not None:
            destination=preview_dir/f"{event['player']}_{period_name(quarter)}_{event['event_id']}.mp4"
            render_clip(source,start,end,destination)
            preview=str(destination)
        rows.append(dict(event_id=event["event_id"],player=event["player"],kind=event["kind"],period=quarter,
                         clock=event["clock"],description=event.get("description",""),source=str(source) if source else "",
                         start=format_video_time(start).rstrip('0').rstrip('.') if start!='' else '',
                         end=format_video_time(end).rstrip('0').rstrip('.') if end!='' else '',
                         status=located["status"],reason=located["reason"],
                         candidates=";".join(map(str,candidates)),preview=preview,
                         source_candidates=json.dumps(located["source_candidates"],ensure_ascii=False)))
        previews_progress.log(describe_clip(rows[-1],index,len(events),names))
        previews_progress.update(index)
    review=output/"review.csv"
    automatic_count=sum(r["status"]=="keep" for r in rows)
    preview_count=sum(bool(r['preview']) for r in rows)
    with (output/"candidates.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    if review.exists():
        with review.open(encoding="utf-8-sig",newline="") as file:
            old=list(csv.DictReader(file))
        if sources_changed:
            for row in old:
                if row['status']=='missing':
                    row.update(status='pending',reason=row['reason']+'；输入源或校准变化，需重新核查缺失')
        rows=preserve_review(old,rows)
        print("保留已有审核表；只追加新事件。刷新数据后请核对事件变化。",flush=True)
    for row in rows:
        for field in ['start','end']:
            if row[field]!='':
                row[field]=format_video_time(parse_video_time(row[field])).rstrip('0').rstrip('.')
    temporary=review.with_suffix(".tmp.csv")
    with temporary.open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    os.replace(temporary,review)
    pending=sum(r["status"]=="pending" for r in rows)
    expected={a['period'] for a in pbp['actions']}
    summary=dict(game_id=args.game_id,quarters=list(videos),partial=highlight_mode or not expected.issubset(videos),
                 source_type="highlights" if highlight_mode else "recording",input_signature=input_signature,
                 sources=[dict(file=str(path),ocr_profile=item["ocr_profile"],source_type=item["source_type"]) for path,item in file_settings.items()],
                 missing=[dict(event_id=r["event_id"],player=r["player"],reason=r["reason"]) for r in rows if r["status"]=="missing"],
                 missing_periods=sorted(expected-set(videos)),events=len(rows),
                 players=summarize_events(events,players),
                 timeline_timings=timings,
                 pending=pending,automatic=automatic_count,
                 automatic_ratio=round(automatic_count/len(events),3) if events else 0,
                 analysis_seconds=round(time.monotonic()-started,2),
                 note="automatic 统计本次算法候选，pending 统计保留人工改动后的审核表；两者可不同")
    atomic_json(output/"analysis.json",summary)
    print(f"分析完成：处理 {len(events)} 段候选，生成 {preview_count} 段预览；审核表有 {pending} 段待确认。审核表：{review}",flush=True)


def review_event(args):
    if args.review:
        review=Path(args.review).expanduser().resolve()
    else:
        reviews=list((ROOT/'output').glob('*/review.csv'))
        if len(reviews)!=1:
            raise ValueError('无法唯一确定审核表；请增加 --review CSV路径')
        review=reviews[0]
    with review.open(encoding='utf-8-sig',newline='') as file:
        reader=csv.DictReader(file);fields=reader.fieldnames;rows=list(reader)
    player=select_keys(load_roster(review.parent),[args.player])[0] if args.player is not None else None
    matches=[r for r in rows if r['event_id']==args.event_id and (player is None or r['player']==player)]
    if not matches:
        raise ValueError('未找到指定球员事件：'+args.event_id)
    if len(matches)!=1:
        raise ValueError('事件属于多个球员；请增加 --player 指定球员')
    return review,fields,rows,matches[0]


class ReviewConflictError(ValueError):
    pass


def save_review(review,fields,rows,expected_revision=None):
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8-sig',newline='',dir=review.parent,delete=False,suffix='.csv') as file:
        temporary=Path(file.name)
        try:
            writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if expected_revision is not None and hashlib.sha256(review.read_bytes()).hexdigest()!=expected_revision:
            raise ReviewConflictError('审核表已在其他窗口或命令行修改。请重新载入，再保存。')
        os.replace(temporary,review)
    finally:
        temporary.unlink(missing_ok=True)


def mark(args):
    if args.status not in ('missing','exclude','pending') or not args.reason.strip():
        raise ValueError('请提供有效状态和人工判断依据')
    review,fields,rows,row=review_event(args)
    row.update(status=args.status,reason=args.reason.strip())
    save_review(review,fields,rows,getattr(args,'expected_revision',None))
    print(f'已记录事件 {args.event_id}：{args.status}；{row["reason"]}\n审核表：{review}',flush=True)


def adjust(args):
    review,fields,rows,row=review_event(args)
    if row['player'] not in load_roster(review.parent) or not re.fullmatch(r'\d+',row['event_id']) or int(row['period'])<1:
        raise ValueError('审核表中的球员或事件编号无效')
    start,end=parse_video_time(args.start),parse_video_time(args.end)
    raw_source=getattr(args,'source',None) or row['source']
    if not raw_source:
        raise ValueError('该回合没有源录像；请增加 --source 路径')
    source=Path(raw_source).expanduser().resolve()
    validate_range(start,end,probe(source))
    preview_dir=review.parent/'previews';preview_dir.mkdir(exist_ok=True)
    preview=preview_dir/f"{row['player']}_{period_name(int(row['period']))}_{row['event_id']}.mp4"
    if getattr(args,'expected_revision',None):
        # Versioned previews keep a failed concurrent save from replacing the previous preview.
        preview=preview.with_name(preview.stem+'_'+args.expected_revision[:12]+preview.suffix)
    if preview.resolve()==source:
        raise ValueError('预览路径与源录像重合；拒绝覆盖源录像')
    render_clip(source,start,end,preview)
    row.update(source=str(source),start=format_video_time(start).rstrip('0').rstrip('.'),end=format_video_time(end).rstrip('0').rstrip('.'),
               status='keep',reason=getattr(args,'reason','').strip() or '人工调整区间并确认保留',preview=str(preview))
    save_review(review,fields,rows,getattr(args,'expected_revision',None))
    print(f"已保存事件 {args.event_id}：{row['start']} → {row['end']}，状态 keep\n审核表：{review}\n更新后的预览：{preview}",flush=True)


def combine_ranges(entries):
    unique={}
    for period,source,start,end,event_id in entries:
        key=(period,event_id)
        if key in unique:
            _,old_source,old_start,old_end,_=unique[key]
            if source!=old_source or start>old_end or end<old_start:
                raise ValueError(f"同一回合 Q{period} 事件 {event_id} 的审核区间冲突；请统一源录像和时间区间")
            start,end=min(start,old_start),max(end,old_end)
        unique[key]=(period,source,start,end,event_id)
    merged=merge_ranges([((period,source),start,end) for period,source,start,end,_ in sorted(unique.values())])
    result=[(period,source,start,end) for (period,source),start,end in merged]
    def order(clip):
        period,source,start,end=clip
        event_id=min(e for p,s,a,b,e in unique.values() if p==period and s==source and a<end and b>start)
        return period,event_id,start
    return sorted(result,key=order)


def review_events(directory,players,rows):
    directory=Path(directory)
    pbp=json.loads((directory/'play_by_play.json').read_text())
    info=json.loads((directory/'source.json').read_text()) if (directory/'source.json').exists() else {}
    if info.get('game_id'):validate_pbp(pbp,info['game_id'])
    analysis=json.loads((directory/'analysis.json').read_text()) if (directory/'analysis.json').exists() else {}
    periods={a['period'] for a in pbp['actions']} if analysis.get('source_type')=='highlights' else set(analysis.get('quarters') or [int(r['period']) for r in rows])
    return select_events(pbp,periods,players)


def export(args):
    scenery=getattr(args,'scenery',False)
    prepend=getattr(args,'prepend',None) or []
    if scenery and prepend:raise ValueError('--scenery 与 --prepend 不能同时使用')
    if scenery:prepend=[str(ROOT/'intro'/'la-scenery.mp4')]
    prepend=[str(Path(path).expanduser().resolve()) for path in prepend]
    watermark=getattr(args,'watermark',True)
    started=time.monotonic()
    review=Path(args.review).expanduser().resolve()
    with review.open(encoding="utf-8-sig",newline="") as file:
        rows=list(csv.DictReader(file))
    if not rows:
        raise ValueError("审核表为空，无法导出")
    people=load_roster(review.parent)
    requested=getattr(args,'players',None)
    if requested is None:requested=load_selection(review.parent)
    keys=select_keys(people,list(dict.fromkeys(r['player'] for r in rows)) if requested is None else requested)
    if (review.parent/'play_by_play.json').exists():
        expected=review_events(review.parent,{key:(people[key]['id'],people[key]['last_name']) for key in keys},rows)
        known={(r['player'],int(r['period']),r['event_id']) for r in rows}
        if any((e['player'],e['period'],e['event_id']) not in known for e in expected):
            raise ValueError('所选球员还有未分析的回合；请在审核台应用球员或重新分析')
    rows=[r for r in rows if r['player'] in keys]
    validate_review(rows)
    atomic_json(review.parent/'selection.json',dict(
        kept=[dict(player=r['player'],event_id=r['event_id'],source=r['source']) for r in rows if r['status']=='keep'],
        missing=[dict(player=r['player'],event_id=r['event_id'],reason=r['reason']) for r in rows if r['status']=='missing'],
        excluded=[dict(player=r['player'],event_id=r['event_id'],reason=r['reason']) for r in rows if r['status']=='exclude']))
    durations={}
    selected=[]
    destination=review.parent/"highlights.mp4"
    counts={player:dict(kept=0,excluded=0,missing=0) for player in keys}
    candidate_path=review.parent/"candidates.csv"
    candidates={}
    if candidate_path.exists():
        with candidate_path.open(encoding="utf-8-sig",newline="") as file:
            candidates={(r["player"],r["period"],r["event_id"]):r for r in csv.DictReader(file)}
    manual_time_edits=0
    manual_resolutions=0
    for row in rows:
        if row["player"] not in people:
            raise ValueError("未知球员："+row["player"])
        if row["status"] in ("exclude","missing"):
            counts[row["player"]]["missing" if row["status"]=="missing" else "excluded"]+=1
            continue
        source=Path(row["source"]).resolve()
        if source in {destination, *(review.parent/f"{name}.mp4" for name in people)}:
            raise ValueError("输出路径与源录像重合；拒绝覆盖源录像")
        if source not in durations:
            durations[source]=probe(source)
        start,end=parse_video_time(row["start"]),parse_video_time(row["end"])
        validate_range(start,end,durations[source])
        candidate=candidates.get((row["player"],row["period"],row["event_id"]))
        if candidate:
            original=(parse_video_time(candidate["start"]),parse_video_time(candidate["end"])) if candidate["start"] and candidate["end"] else None
            manual_time_edits+=original!=(start,end)
            manual_resolutions+=candidate["status"]=="pending"
        period,event_id=int(row["period"]),int(row["event_id"])
        if period<1 or event_id<0:
            raise ValueError("审核表中的节次或事件编号无效")
        selected.append((period,str(source),start,end,event_id))
        counts[row["player"]]["kept"]+=1
    if not selected:
        raise ValueError("没有保留片段，无法生成集锦")
    ranges=combine_ranges(selected)
    opening=[]
    for raw in prepend:
        path=Path(raw)
        if path==destination:
            raise ValueError("输出路径与开场重合；拒绝覆盖开场")
        duration=probe(path,require_audio=False)
        validate_range(0,duration,duration)
        opening.append((0,str(path),0,duration))
    intro_seconds=sum(end for _,_,_,end in opening)
    from nba_highlight_maker.cover import generate_cover
    cover_path=generate_cover(review.parent,review=review,players=keys)
    seconds=intro_seconds+sum(end-start for _,_,start,end in ranges)
    names={key:p['name'] for key,p in people.items()}
    total_clips=len(ranges)+len(opening)
    for player in keys:
        print(f"{names[player]}：保留 {counts[player]['kept']} 条，排除 {counts[player]['excluded']} 条，确认缺失 {counts[player]['missing']} 条",flush=True)
    unique_events=len({(p,e) for p,_,_,_,e in selected})
    print(f"合并导出：{len(selected)} 条审核记录 → {unique_events} 个回合 → {len(ranges)} 段比赛；开场 {intro_seconds:.3f} 秒，预计成片 {format_video_time(seconds)}",flush=True)
    print(f"导出选项：前置视频 {len(opening)} 段；2K 水印 {'开启' if watermark else '关闭'}",flush=True)
    print(f"导出任务：编码 {total_clips} 段（含开场），拼接 1 条集锦。百分比对应当前编码或拼接阶段。",flush=True)
    with tempfile.TemporaryDirectory(prefix="nba-export-",dir=review.parent) as folder:
        directory=Path(folder)
        clips=[]
        for i,(_,source,start,end) in enumerate([*opening,*ranges]):
            destination=directory/f"{i:04d}.mp4"
            print(f"编码 [{i+1}/{total_clips}]：{Path(source).name} {format_video_time(start)} → {format_video_time(end)}",flush=True)
            options=dict(allow_silent=True) if i<len(opening) else {}
            render_clip(source,start,end,destination,progress=Progress(f"片段编码 {i+1}/{total_clips}",end-start,'秒'),watermark=watermark and i>=len(opening),**options)
            clips.append(destination)
        concat=directory/"concat.txt"
        concat.write_text("".join(f"file '{path.name}'\n" for path in clips))
        temporary=directory/"highlights.mp4"
        run(["ffmpeg","-v","error","-f","concat","-safe","1","-i",concat,
             "-c","copy","-movflags","+faststart","-y",temporary],
            progress=Progress("集锦拼接",seconds,'秒'))
        probe(temporary)
        destination=review.parent/"highlights.mp4"
        os.replace(temporary,destination)
        print("已导出",destination,flush=True)
    atomic_json(review.parent/"export.json",dict(players=counts,selected_players=keys,
                output=str(destination),unique_events=unique_events,shared_duplicates=len(selected)-unique_events,
                cover=str(cover_path),
                cover_4x3=str(review.parent/'cover_4x3.png'),cover_3x4=str(review.parent/'cover_3x4.png'),title=str(review.parent/'title.txt'),description=str(review.parent/'description.txt'),
                title_douyin=str(review.parent/'title_douyin.txt'),
                merged_clips=len(ranges),prepend=prepend,intro_seconds=intro_seconds,scenery_enabled=scenery,
                opening_segments=[dict(source=s,start=a,end=b) for _,s,a,b in opening],
                watermark=dict(enabled=watermark,asset=str(ROOT/'assets'/'nba2k-logo.png'),width=300,right=32,top=32,intro=False),
                expected_duration_seconds=seconds,
                segments=[dict(period=p,source=s,start=a,end=b) for p,s,a,b in ranges],
                manual_time_edits=manual_time_edits if candidates else None,
                manual_resolutions=manual_resolutions if candidates else None,
                review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
                export_seconds=round(time.monotonic()-started,2)))


