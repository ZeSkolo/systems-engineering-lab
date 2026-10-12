#!/usr/bin/env python3
"""Loopback-only educational UI server. No shell execution or public deployment."""
from __future__ import annotations
import argparse, ast, dataclasses, importlib.util, json, mimetypes, os, re, shlex, sqlite3, subprocess, sys, tempfile, time
from datetime import date
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'frontend/project.json').read_text())
DATA = Path(os.environ.get('PROJECT_UI_DATA', str(ROOT / '.web-data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
AGENTS, STORES, MODULES = {}, {}, {}

def load_module(path):
    path = (ROOT / path).resolve()
    if path not in MODULES:
        name = '_project_' + str(len(MODULES))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        MODULES[path] = module
    return MODULES[path]

def text(payload, key, max_len=12000):
    value = payload.get(key, '')
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ValueError(f'{key}: enter 1–{max_len} characters')
    return value.strip()

def number(payload, key, low, high):
    v = payload.get(key)
    if isinstance(v, bool): raise ValueError(f'{key}: enter a number')
    try: n = int(str(v))
    except (TypeError, ValueError): raise ValueError(f'{key}: enter a whole number')
    if n < low or n > high: raise ValueError(f'{key}: must be between {low} and {high}')
    return n

def command(args, cwd, timeout=10):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=False)
    if p.returncode: raise ValueError((p.stderr or p.stdout or f'Process exited {p.returncode}')[:16000])
    return p.stdout[:100000]

def build_native(base, binary):
    base = ROOT / base
    if not (base / binary).exists(): command(['make'], base, 30)
    return base, base / binary

def database(base, action, p):
    sys.path.insert(0, str(ROOT / base))
    from minidb.engine import MiniDB
    db = MiniDB(str(DATA / ('lab.db' if base != '.' else 'mini.db')))
    try:
        if action == 'stats':
            return {'rows': [{'pages': db.pager.page_count, 'buffer_hits': db.pool.hits, 'buffer_misses': db.pool.misses}], 'message': 'Storage statistics'}
        result = db.execute(text(p, 'sql'))
        return {'columns': result.columns, 'rows': result.rows, 'message': str(result)}
    finally: db.close()

def redis(base, action, p):
    module = load_module(str(Path(base) / 'python-runtime/mini_redis.py'))
    if base not in STORES:
        store = module.RedisStore(); store.start(); STORES[base] = store
    store = STORES[base]
    if action == 'keys':
        return {'rows': [{'key': k, 'value': store.get(k), 'ttl_ms': store.ttl_ms(k)} for k in store.keys('*')], 'message': 'Live in-process Python runtime; values reset when the UI server stops.'}
    parts = shlex.split(text(p, 'command', 8000))
    if not parts: raise ValueError('Enter a Redis command')
    allowed = {'PING','ECHO','SET','GET','DEL','EXISTS','EXPIRE','PEXPIRE','TTL','PTTL','KEYS','INCR','DECR','INCRBY','MGET','MSET','TYPE','DBSIZE'}
    if parts[0].upper() not in allowed: raise ValueError('Command not allowed in UI; use the CLI for FLUSHDB/QUIT')
    processor = module.CommandProcessor(store)
    parser = module.RespParser()
    parser.feed(processor.dispatch(parts))
    result = parser.next()
    if isinstance(result, module.RespError): raise ValueError(str(result))
    return {'output': result, 'message': 'Executed against the repository’s Python Redis runtime'}

def agent(base, action, p):
    sys.path.insert(0, str(ROOT / base))
    from agent.react import Agent
    from agent.llm import MockLLM
    if base not in AGENTS: AGENTS[base] = Agent(str(DATA / 'agent'), llm=MockLLM())
    a = AGENTS[base]
    if action == 'recall': return {'output': a.memory.recall(text(p, 'message', 4000)), 'message': 'Matching persistent memories'}
    r = a.chat(text(p, 'message', 4000))
    return {'output': r.answer, 'facts': r.extracted, 'message': 'Repository heuristic MockLLM (offline), not a live language model'}

def search(base, action, p):
    m = load_module(str(Path(base) / 'aggregator/metasearch.py'))
    mode = p.get('mode', 'offline')
    if mode not in {'offline','live'}: raise ValueError('Choose offline or live search')
    rows = [dataclasses.asdict(r) for r in m.search(text(p,'query',300), ['catalog'] if mode == 'offline' else ['catalog','wikipedia'])]
    return {'rows': rows, 'message': 'Local educational catalog' if mode == 'offline' else 'Catalog + live Wikipedia; engine failures are shown explicitly'}

def compiler(base, action, p):
    basepath, binary = build_native(base, 'minic')
    if action == 'examples':
        return {'rows': [{'name': f.name, 'source': f.read_text()} for f in sorted((basepath/'examples').glob('*.c'))], 'message': 'Repository compiler examples'}
    source = text(p, 'source', 10000)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td)/'input.c'; f.write_text(source)
        output = command([str(binary), str(f)], basepath, 4)
    return {'output': output, 'message': 'Real x86-64 assembly from minic. Submitted programs are NOT executed.'}

def snapshot(action, p):
    base, binary = build_native('.', 'bin/snapstore')
    store = DATA/'snapshot-store'
    def run(*args): return command([str(binary), *map(str,args)],base,5)
    if action == 'init':
        if store.exists(): raise ValueError('Store already exists; initialization would overwrite data')
        return {'output': run('init',store,number(p,'blocks',1,1024)), 'message':'Store initialized'}
    if not store.exists(): raise ValueError('Initialize a store first')
    if action in {'stats','list','verify'}: return {'output':run(action,store),'message':action.title()+' completed'}
    if action in {'snapshot','rollback','delete-snapshot'}:
        name=text(p,'name',40)
        if not re.fullmatch(r'[A-Za-z0-9_-]+',name): raise ValueError('Use only letters, numbers, underscores or hyphens')
        return {'output':run(action,store,name),'message':action+' completed'}
    block=number(p,'block',0,1023)
    with tempfile.TemporaryDirectory() as td:
        f=Path(td)/'payload'
        if action=='write':
            value=text(p,'content',4096).encode()
            if len(value)>4096: raise ValueError('Maximum 4096 bytes per block')
            f.write_bytes(value)
            return {'output':run('write',store,block,f),'message':'Block written'}
        if action=='read':
            run('read',store,block,f)
            return {'output':f.read_bytes().decode('utf-8','replace'),'message':'Block read'}
    raise ValueError('Unknown snapshot action')

def expenses(action,p):
    sys.path.insert(0,str(ROOT))
    from expense_tracker.db import ExpenseDatabase
    from expense_tracker.validation import parse_amount, parse_date, clean_category, clean_description
    with ExpenseDatabase(DATA/'expenses.db') as db:
        if action=='add':
            amount=parse_amount(text(p,'amount',32)); category=clean_category(text(p,'category',50)); day=parse_date(text(p,'date',10))
            note=p.get('description','')
            if not isinstance(note,str) or len(note)>500: raise ValueError('Description must be at most 500 characters')
            note=clean_description(note)
            eid=db.add_expense(amount,category,note,day)
            return {'message':f'Expense #{eid} saved','rows':[dict(r) for r in db.list_expenses()]}
        if action=='delete':
            if not db.delete_expense(number(p,'id',1,2147483647)): raise ValueError('Expense not found')
            return {'message':'Expense deleted','rows':[dict(r) for r in db.list_expenses()]}
        if action=='reports': return {'rows':[dict(r) for r in db.category_totals(p.get('month') or None)],'monthly':[dict(r) for r in db.monthly_totals()],'message':'Amounts in exact cents; no currency assumed'}
        if action=='list': return {'rows':[dict(r) for r in db.list_expenses(p.get('month') or None)],'message':'Saved expenses; amounts in exact cents'}
    raise ValueError('Unknown expense action')

def school(action,p):
    # Use actual activity data from the original API without requiring FastAPI to run this separate UI.
    tree=ast.parse((ROOT/'src/app.py').read_text())
    seed=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='activities' for t in n.targets))
    with sqlite3.connect(DATA/'school.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS members (activity TEXT, email TEXT, PRIMARY KEY(activity,email))')
        db.execute('CREATE TABLE IF NOT EXISTS initialized (id INTEGER PRIMARY KEY)')
        if not db.execute('SELECT 1 FROM initialized').fetchone():
            db.executemany('INSERT OR IGNORE INTO members VALUES (?,?)',[(n,e) for n,a in seed.items() for e in a['participants']]);db.execute('INSERT INTO initialized VALUES (1)')
        if action=='signup':
            name=text(p,'activity',100);email=text(p,'email',200).lower()
            if name not in seed: raise ValueError('Activity not found')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): raise ValueError('Enter a valid email address')
            if db.execute('SELECT 1 FROM members WHERE activity=? AND email=?',(name,email)).fetchone(): raise ValueError('Already registered')
            if db.execute('SELECT count(*) FROM members WHERE activity=?',(name,)).fetchone()[0]>=seed[name]['max_participants']: raise ValueError('This activity is full')
            db.execute('INSERT INTO members VALUES (?,?)',(name,email))
        elif action!='list': raise ValueError('Unknown school action')
        rows=[{'activity':name,'description':a['description'],'schedule':a['schedule'],'capacity':a['max_participants'],'registered':db.execute('SELECT count(*) FROM members WHERE activity=?',(name,)).fetchone()[0]} for name,a in seed.items()]
    return {'rows':rows,'message':'Persistent local registration backend based on the original API’s activities'}

def fitness(action,p):
    with sqlite3.connect(DATA/'fitness.db') as db:
        db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS workouts (id INTEGER PRIMARY KEY, name TEXT, team TEXT, activity TEXT, minutes INTEGER, day TEXT)')
        if action=='add':
            day=text(p,'date',10); date.fromisoformat(day)
            activity=text(p,'activity',30)
            if activity not in {'Walking','Running','Strength','Cycling'}: raise ValueError('Unsupported activity')
            db.execute('INSERT INTO workouts(name,team,activity,minutes,day) VALUES (?,?,?,?,?)',(text(p,'name',80),text(p,'team',80),activity,number(p,'minutes',1,600),day))
        elif action=='delete': db.execute('DELETE FROM workouts WHERE id=?',(number(p,'id',1,2147483647),))
        elif action not in {'list','leaderboard'}: raise ValueError('Unknown fitness action')
        if action=='leaderboard': rows=[dict(r) for r in db.execute('SELECT name,team,SUM(minutes) AS total_minutes,COUNT(*) AS sessions FROM workouts GROUP BY name,team ORDER BY total_minutes DESC')]
        else: rows=[dict(r) for r in db.execute('SELECT * FROM workouts ORDER BY day DESC,id DESC LIMIT 500')]
    return {'rows':rows,'message':'Local SQLite prototype; not the unfinished Django/MongoDB course stack'}

def preview(action,p):
    allowed=CONFIG.get('previews',[])
    if action=='list':return {'rows':allowed,'message':'Original frontend files preserved; previews are sandboxed'}
    requested=text(p,'page',200)
    page=next((r for r in allowed if r['path']==requested),None)
    if page is None:raise ValueError('Select a listed page')
    return {'preview':'/preview/'+page['path'],'message':'Original project output: '+page['label']}

def dispatch(tool_id,action,p):
    tool=next((t for t in CONFIG['tools'] if t['id']==tool_id),None)
    if tool is None or action not in [a['id'] for a in tool['actions']]:raise ValueError('Unknown tool/action')
    kind=tool['kind'];base=tool.get('base','.')
    if kind=='database':return database(base,action,p)
    if kind=='redis':return redis(base,action,p)
    if kind=='agent':return agent(base,action,p)
    if kind=='search':return search(base,action,p)
    if kind=='compiler':return compiler(base,action,p)
    if kind=='snapshot':return snapshot(action,p)
    if kind=='expenses':return expenses(action,p)
    if kind=='school':return school(action,p)
    if kind=='fitness':return fitness(action,p)
    if kind=='preview':return preview(action,p)
    raise ValueError('Unsupported project')

class Handler(BaseHTTPRequestHandler):
    server_version='ProjectUI/1.0'
    def allowed_host(self):
        return self.headers.get('Host','') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
    def send_bytes(self,status,body,mime='application/json'):
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Cache-Control','no-store')
        self.end_headers();self.wfile.write(body)
    def send_json(self,status,obj):self.send_bytes(status,json.dumps(obj,ensure_ascii=False).encode(),'application/json; charset=utf-8')
    def do_GET(self):
        if not self.allowed_host():return self.send_json(403,{'error':'Localhost only'})
        path=unquote(urlparse(self.path).path)
        if path=='/api/config':return self.send_json(200,CONFIG)
        if path=='/api/health':return self.send_json(200,{'ok':True,'project':CONFIG['title']})
        if path=='/':path='/frontend/index.html'
        if path.startswith('/frontend/'):
            file=(ROOT/path.lstrip('/')).resolve();safe=file.is_relative_to(ROOT/'frontend') and file.suffix in {'.html','.css','.js','.json'}
        elif path.startswith('/preview/'):
            file=(ROOT/path[len('/preview/'):]).resolve()
            roots=[(ROOT/r['root']).resolve() for r in CONFIG.get('previews',[])]
            safe=any(file.is_relative_to(r) for r in roots) and file.suffix.lower() in {'.html','.css','.js','.png','.jpg','.jpeg','.gif','.svg','.webp','.mp3','.mp4','.woff','.woff2','.ico'} and not any(part.startswith('.') for part in file.relative_to(ROOT).parts)
        else:return self.send_json(404,{'error':'Not found'})
        if not safe or not file.is_file():return self.send_json(404,{'error':'Not found'})
        mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        self.send_bytes(200,file.read_bytes(),mime)
    def do_POST(self):
        if not self.allowed_host():return self.send_json(403,{'error':'Localhost only'})
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:return self.send_json(403,{'error':'Cross-origin request blocked'})
        if self.headers.get('X-Project-Request')!='1':return self.send_json(403,{'error':'Missing local UI request header'})
        if urlparse(self.path).path!='/api/run':return self.send_json(404,{'error':'Not found'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<2 or length>65536:raise ValueError('Request must be 2–65536 bytes')
            if not self.headers.get('Content-Type','').startswith('application/json'):raise ValueError('Use application/json')
            p=json.loads(self.rfile.read(length))
            if not isinstance(p,dict) or not isinstance(p.get('values',{}),dict):raise ValueError('Invalid request')
            t=time.monotonic();result=dispatch(p.get('tool'),p.get('action'),p.get('values',{}))
            result['elapsed_ms']=round((time.monotonic()-t)*1000,1);return self.send_json(200,result)
        except subprocess.TimeoutExpired:return self.send_json(408,{'error':'Operation timed out'})
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:return self.send_json(400,{'error':str(e)[:4000]})
        except Exception as e:
            print(f'Backend error: {type(e).__name__}: {e}',file=sys.stderr)
            return self.send_json(500,{'error':'Backend operation failed. See server log.'})

def main():
    parser=argparse.ArgumentParser(description='Run this repository’s local frontend and backend')
    parser.add_argument('--port',type=int,default=CONFIG['port']);args=parser.parse_args()
    server=HTTPServer(('127.0.0.1',args.port),Handler)
    print(f"{CONFIG['title']}: http://127.0.0.1:{server.server_port}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        for store in STORES.values():store.stop()
if __name__=='__main__':main()
