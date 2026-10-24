"""Local NBA review console. CSV remains the source of truth."""
from collections import Counter
import csv
import hashlib
import io
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shlex
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import webbrowser

from nba_highlight_maker import processing as nh
from nba_highlight_maker.cover import cover_info,NAMES
from nba_highlight_maker.players import roster,load_roster,load_selection,select_keys


ConflictError=nh.ReviewConflictError
PUBLICATION_FILES={'cover.png','cover_4x3.png','cover_3x4.png','title.txt','title_douyin.txt','description.txt','highlights.mp4'}


def byte_range(header,size):
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
    if not match or not any(match.groups()) or size<=0:
        raise ValueError('无效的媒体范围')
    first,last=match.groups()
    start=int(first) if first else max(0,size-int(last))
    end=min(int(last),size-1) if first and last else size-1
    if not 0<=start<=end<size:
        raise ValueError('媒体范围超出文件长度')
    return start,end


class ReviewStore:
    def __init__(self,root=nh.ROOT,selected=None):
        self.root=Path(root).resolve()
        self.selected=Path(selected).expanduser().resolve() if selected else None
        self.reviews={}
        self.media={}
        self.lock=threading.RLock()

    def library(self):
        paths=set((self.root/'output').rglob('review.csv'))
        if self.selected:
            if not self.selected.is_file():raise ValueError('审核表不存在：'+str(self.selected))
            paths.add(self.selected)
        boards=[]
        for path in sorted(paths):
            path=path.resolve()
            identifier=hashlib.sha256(str(path).encode()).hexdigest()[:16]
            self.reviews[identifier]=path
            try:
                relative=path.parent.relative_to(self.root/'output')
                label=str(relative)
                game_directory=self.root/'output'/relative.parts[0]
            except ValueError:
                label=path.parent.name
                game_directory=path.parent
            metadata=path.parent/'source.json'
            if not metadata.is_file():metadata=game_directory/'source.json'
            try:
                game=cover_info(json.loads(metadata.read_text()))
                if re.fullmatch(r'\d{10}',game_directory.name) and game['game_id']!=game_directory.name:
                    raise ValueError('比赛来源与目录不匹配')
                version=path.parent.name if path.parent!=game_directory else '默认版本'
                if version.startswith('youtube-'):version='YouTube 集锦'
                elif version=='multi-source':version='多源版本'
                label=f"{game['date']} {game['away_name']} vs {game['home_name']} · {version}"
            except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
            boards.append(dict(id=identifier,label=label,selected=path==self.selected))
        totals=Counter(board['label'] for board in boards)
        seen=Counter()
        for board in boards:
            label=board['label']
            if totals[label]>1:
                seen[label]+=1
                board['label']=f'{label} {seen[label]}'
        return boards

    def read(self,identifier):
        if identifier not in self.reviews:raise ValueError('审核项目不存在，请刷新列表')
        path=self.reviews[identifier]
        content=path.read_bytes()
        reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig'),newline=''))
        fields=reader.fieldnames;rows=list(reader)
        required=set(nh.FIELDS)-{'source_candidates'}
        if not fields or not required.issubset(fields):raise ValueError('审核表缺少必要列')
        return path,fields,rows,hashlib.sha256(content).hexdigest()

    def media_url(self,raw):
        if not raw:return ''
        path=Path(raw).expanduser().resolve()
        if path.suffix.lower()!='.mp4' or not path.is_file():return ''
        identifier=hashlib.sha256(str(path).encode()).hexdigest()[:20]
        self.media[identifier]=path
        return '/media/'+identifier

    def sources(self,path,rows):
        raw={row['source'] for row in rows if row.get('source')}
        analysis=path.parent/'analysis.json'
        if analysis.exists():
            data=json.loads(analysis.read_text())
            raw.update(item.get('file','') for item in data.get('sources',[]))
        for row in rows:
            if row.get('source_candidates'):
                candidates=json.loads(row['source_candidates'])
                raw.update(item['source'] for item in candidates)
        return sorted({str(Path(value).expanduser().resolve()) for value in raw if value})

    def publication_file(self,identifier,name):
        if identifier not in self.reviews or name not in PUBLICATION_FILES:
            raise ValueError('投稿素材不存在')
        directory=self.reviews[identifier].parent.resolve()
        path=(directory/name).resolve()
        if path.parent!=directory or not path.is_file():
            raise ValueError('投稿素材文件缺失或路径无效')
        return path

    def opening_files(self):
        directory=self.root/'media'
        if directory.is_symlink():return []
        extensions={'.mp4','.mov','.m4v','.mkv','.webm','.avi','.ts','.mts','.m2ts','.mpg','.mpeg'}
        return [dict(name=str(path.relative_to(directory)),path=str(path))
                for path in sorted(directory.rglob('*'))
                if path.suffix.lower() in extensions and path.is_file() and directory in path.resolve().parents]

    def select_opening(self,data):
        with self.lock:
            path,_,_,revision=self.read(data['id'])
            if data.get('revision')!=revision:raise ConflictError('审核表已修改，请重新载入')
            paths=data.get('prepend')
            available={item['path'] for item in self.opening_files()}|set(self.snapshot(data['id'])['prepend'])
            if not isinstance(paths,list) or any(not isinstance(p,str) or p not in available for p in paths):
                raise ValueError('请选择 media 目录中可用的视频')
            if len(set(paths))!=len(paths):raise ValueError('前置视频不能重复')
            nh.atomic_json(path.parent/'opening.json',dict(prepend=paths))
            return self.snapshot(data['id'])

    def publication(self,identifier,revision):
        directory=self.reviews[identifier].parent
        files={}
        for name in PUBLICATION_FILES:
            try:files[name]=self.publication_file(identifier,name)
            except ValueError:pass
        covers=[dict(name=name,ratio=ratio,url=f'/files/{identifier}/{name}',path=str(files[name]))
                for name,ratio in [('cover.png','16:9'),('cover_4x3.png','4:3'),('cover_3x4.png','3:4')] if name in files]
        def content(name):return files[name].read_text(encoding='utf-8-sig').strip() if name in files else ''
        export_revision=None
        export_players=None
        options=dict(prepend=[],watermark=True)
        try:
            metadata=json.loads((directory/'export.json').read_text())
            if isinstance(metadata,dict):
                export_revision=metadata.get('review_sha256')
                export_players=metadata.get('selected_players') or list(metadata.get('players',{})) or None
                prepend=metadata.get('prepend')
                if isinstance(prepend,list) and all(isinstance(path,str) for path in prepend):
                    options['prepend']=prepend
                if isinstance(metadata.get('watermark'),dict):
                    options['watermark']=metadata['watermark'].get('enabled',True) is not False
        except (OSError,ValueError):pass
        title=content('title.txt');description=content('description.txt')
        video=dict(path=str(files['highlights.mp4']),url=f'/files/{identifier}/highlights.mp4') if 'highlights.mp4' in files else None
        if not files:state='missing'
        elif not (covers and title and video):state='incomplete'
        elif not export_revision:state='unverified'
        else:state='ready' if export_revision==revision else 'stale'
        return dict(state=state,directory=str(directory),covers=covers,title=title,douyin_title=content('title_douyin.txt'),description=description,video=video,export_options=options,export_players=export_players)

    def snapshot(self,identifier):
        with self.lock:
            path,_,rows,revision=self.read(identifier)
            candidate_path=path.parent/'candidates.csv'
            candidates={}
            if candidate_path.exists():
                with candidate_path.open(encoding='utf-8-sig',newline='') as file:
                    candidates={(r['player'],r['period'],r['event_id']):r for r in csv.DictReader(file)}
            fresh=list(candidates.values())
            sources=self.sources(path,[*rows,*fresh])
            filenames=Counter(Path(raw).name for raw in sources)
            views=[]
            for index,row in enumerate(rows):
                candidate=candidates.get((row['player'],row['period'],row['event_id']),row)
                options=json.loads(candidate.get('source_candidates') or '[]')
                if not options and candidate.get('source'):
                    options=[dict(source=candidate['source'],t=nh.parse_video_time(t)) for t in candidate.get('candidates','').split(';') if t]
                views.append(dict(row,index=index,source_url=self.media_url(row['source']),preview_url=self.media_url(row['preview']),options=options,
                                  period_label=nh.period_name(int(row['period'])),clock_label=nh.format_video_time(nh.parse_clock(row['clock'])) if row['clock'] else '—'))
            info_path=path.parent/'source.json'
            info=json.loads(info_path.read_text()) if info_path.exists() else {}
            game=info.get('game',{})
            away=info.get('away') or game.get('awayTeam',{}).get('teamTricode','')
            home=info.get('home') or game.get('homeTeam',{}).get('teamTricode','')
            counts={status:sum(r['status']==status for r in rows) for status in ['keep','pending','missing','exclude']}
            people=roster(info)
            publication=self.publication(identifier,revision)
            opening_path=path.parent/'opening.json'
            prepend=json.loads(opening_path.read_text())['prepend'] if opening_path.exists() else publication['export_options']['prepend']
            if not isinstance(prepend,list) or any(not isinstance(p,str) for p in prepend):
                raise ValueError('前置视频编排记录无效')
            if publication['state']=='ready' and prepend!=publication['export_options']['prepend']:
                publication['state']='stale'
            saved=load_selection(path.parent)
            selected=select_keys(people,saved) if saved is not None else [key for key in (publication['export_players'] or list(dict.fromkeys(r['player'] for r in rows))) if key in people]
            if publication['state']=='ready' and publication['export_players'] is not None and set(publication['export_players'])!=set(selected):
                publication['state']='stale'
            return dict(id=identifier,revision=revision,review=str(path),title=f'{away} @ {home}' if away and home else path.parent.name,
                        game_id=info.get('game_id',''),rows=views,counts=counts,publication=publication,
                        opening_files=self.opening_files(),prepend=prepend,
                        roster=[dict(p,key=key,team_label=NAMES.get(p['team'],p['team'])) for key,p in people.items()],selected_players=selected,
                        sources=[dict(path=raw,name=Path(raw).name if filenames[Path(raw).name]==1 else Path(raw).parent.name+'/'+Path(raw).name,url=self.media_url(raw)) for raw in sources],
                        export_command=' '.join(shlex.quote(part) for part in ['python3',str(self.root/'scripts'/'nba_highlights.py'),'export','--review',str(path)]))

    def select_players(self,data):
        with self.lock:
            identifier=data['id']
            path,fields,rows,revision=self.read(identifier)
            if data.get('revision')!=revision:raise ConflictError('审核表已修改，请重新载入')
            people=load_roster(path.parent)
            keys=select_keys(people,data.get('players'))
            if 'players' not in data:raise ValueError('请勾选出场球员')
            events=nh.review_events(path.parent,{key:(people[key]['id'],people[key]['last_name']) for key in keys},rows)
            sources=self.sources(path,rows)
            caches={}
            for cache in path.parent.glob('ocr_*.json'):
                try:
                    saved=json.loads(cache.read_text());signature=saved['signature'];source=signature['path']
                    if source not in sources:continue
                    stat=Path(source).stat()
                    if signature.get('size')!=stat.st_size or signature.get('mtime_ns')!=stat.st_mtime_ns:continue
                    if signature.get('ocr')!=hashlib.sha256((self.root/'native'/'vision_ocr.swift').read_bytes()).hexdigest():continue
                    caches[Path(source)]=saved['samples']
                except (OSError,ValueError,KeyError,TypeError):continue
            known={(r['player'],int(r['period']),r['event_id']) for r in rows}
            added=[]
            for event in events:
                if (event['player'],event['period'],event['event_id']) in known:continue
                located=nh.locate_sources(event,list(caches),caches) if caches else dict(source=None,candidates=[],source_candidates=[])
                source=located['source'] or next((r['source'] for r in rows if int(r['period'])==event['period'] and r['source']),sources[0] if sources else '')
                t=located['candidates'][0] if located['candidates'] else None
                row=dict.fromkeys(nh.FIELDS,'')
                row.update(event_id=event['event_id'],player=event['player'],kind=event['kind'],period=event['period'],clock=event['clock'],description=event.get('description',''),
                           source=str(source) if source else '',status='pending',reason='候选待审核' if t is not None else '待定位',
                           start=nh.format_video_time(max(0,t-8)) if t is not None else '',end=nh.format_video_time(t+3) if t is not None else '',
                           candidates=';'.join(map(str,located['candidates'])),source_candidates=json.dumps(located['source_candidates']))
                added.append(row)
            if added:nh.save_review(path,list(dict.fromkeys([*fields,*nh.FIELDS])),rows+added,revision)
            nh.atomic_json(path.parent/'players.json',dict(selected_players=keys))
            result=self.snapshot(identifier)
            result['selected_players']=keys
            return result

    def decide(self,data):
        with self.lock:
            identifier=data['id']
            path,_,rows,revision=self.read(identifier)
            if data.get('revision')!=revision:raise ConflictError('审核表已在其他窗口或命令行修改。请重新载入，再保存。')
            index=data['index']
            if type(index) is not int or not 0<=index<len(rows):raise ValueError('回合编号无效')
            row=rows[index]
            status=data['status']
            if not isinstance(data.get('reason',''),str):raise ValueError('审核依据必须是文本')
            reason=data.get('reason','').strip()
            args=SimpleNamespace(review=str(path),event_id=row['event_id'],player=row['player'],reason=reason,expected_revision=revision)
            if status=='keep':
                # Only sources already registered in this review can be read by the console.
                available=self.snapshot(identifier)['sources']
                source=str(Path(data.get('source') or row['source']).expanduser().resolve())
                if source not in {item['path'] for item in available}:raise ValueError('请选择该审核项目中已登记的源录像')
                args.source=source;args.start=data['start'];args.end=data['end']
                nh.adjust(args)
            elif status in ('missing','exclude','pending'):
                args.status=status
                nh.mark(args)
            else:raise ValueError('未知审核状态')
            return self.snapshot(identifier)


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass

    def trusted(self):
        expected=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=expected:
            self.send_error(403,'Invalid host');return False
        origin=self.headers.get('Origin')
        if origin and origin!='http://'+expected:
            self.send_error(403,'Invalid origin');return False
        return True

    def json_response(self,status,data):
        content=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if not self.trusted():return
        route=urlparse(self.path)
        try:
            if route.path=='/':
                content=(nh.ROOT/'web'/'nba-review.html').read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(content)
            elif route.path=='/api/reviews':
                self.json_response(200,self.server.store.library())
            elif route.path=='/api/review':
                self.json_response(200,self.server.store.snapshot(parse_qs(route.query).get('id',[''])[0]))
            elif route.path=='/api/review.csv':
                with self.server.store.lock:
                    path,_,_,_=self.server.store.read(parse_qs(route.query).get('id',[''])[0])
                    content=path.read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="review.csv"')
                self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(content)
            elif route.path.startswith('/files/'):
                parts=route.path.split('/')
                if len(parts)!=4 or parts[3] not in PUBLICATION_FILES:
                    self.send_error(404);return
                self.send_file(self.server.store.publication_file(parts[2],parts[3]))
            elif route.path.startswith('/media/'):
                self.send_media(route.path.removeprefix('/media/'))
            else:self.send_error(404)
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.json_response(400,dict(error=str(error)))

    def do_POST(self):
        if not self.trusted():return
        if self.path not in ('/api/decision','/api/players','/api/opening'):self.send_error(404);return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':self.send_error(415);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:raise ValueError('请求长度无效')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('审核请求必须是对象')
            handler={'/api/players':self.server.store.select_players,'/api/opening':self.server.store.select_opening,'/api/decision':self.server.store.decide}[self.path]
            self.json_response(200,handler(data))
        except ConflictError as error:self.json_response(409,dict(error=str(error)))
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:self.json_response(400,dict(error=str(error)))

    def send_media(self,identifier,head=False):
        path=self.server.store.media.get(identifier)
        if path is None or not path.is_file():self.send_error(404);return
        self.send_file(path,head)

    def send_file(self,path,head=False):
        with path.open('rb') as source:
            size=path.stat().st_size
            try:
                start,end=byte_range(self.headers['Range'],size) if self.headers.get('Range') else (0,size-1)
            except ValueError:
                self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
            self.send_response(206 if self.headers.get('Range') else 200)
            self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream');self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','no-cache')
            if self.headers.get('Range'):self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if head:return
            source.seek(start);remaining=end-start+1
            try:
                while remaining:
                    part=source.read(min(remaining,65536))
                    if not part:break
                    self.wfile.write(part);remaining-=len(part)
            except (BrokenPipeError,ConnectionResetError):pass

    def do_HEAD(self):
        if not self.trusted():return
        route=urlparse(self.path)
        if route.path.startswith('/media/'):self.send_media(route.path.removeprefix('/media/'),head=True)
        else:self.send_error(404)


def serve(args):
    store=ReviewStore(selected=args.review)
    if not store.library():raise ValueError('没有 review.csv；请先执行 analyze')
    if not 0<=args.port<=65535:raise ValueError('端口必须在 0—65535 之间')
    with ThreadingHTTPServer(('127.0.0.1',args.port),ReviewHandler) as server:
        server.store=store
        url=f'http://127.0.0.1:{server.server_port}/'
        print(f'NBA 审核控制台：{url}\n保存直接写入审核 CSV。按 Ctrl+C 停止。',flush=True)
        if not args.no_open:webbrowser.open(url)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
