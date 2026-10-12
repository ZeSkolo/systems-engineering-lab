import json, os, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error, socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CONFIG=json.loads((ROOT/'frontend/project.json').read_text())

class ProjectIntegrationTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory()
  with socket.socket() as s:s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1]
  cls.base=f'http://127.0.0.1:{cls.port}'
  cls.log=tempfile.TemporaryFile(mode='w+')
  cls.process=subprocess.Popen([sys.executable,str(ROOT/'backend/server.py'),'--port',str(cls.port)],cwd=ROOT,env={**os.environ,'PROJECT_UI_DATA':cls.temp.name},stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:
    urllib.request.urlopen(cls.base+'/api/health',timeout=1);return
   except Exception:
    if cls.process.poll() is not None:break
    time.sleep(.05)
  cls.log.seek(0);raise RuntimeError(cls.log.read())
 @classmethod
 def tearDownClass(cls):
  cls.process.terminate()
  try:cls.process.wait(timeout=5)
  except subprocess.TimeoutExpired:cls.process.kill();cls.process.wait()
  cls.log.close();cls.temp.cleanup()
 def request(self,tool,action,values={},headers=None):
  req=urllib.request.Request(self.base+'/api/run',data=json.dumps(dict(tool=tool,action=action,values=values)).encode(),headers=headers or {'Content-Type':'application/json','X-Project-Request':'1'})
  with urllib.request.urlopen(req,timeout=45) as r:return json.load(r)
 def test_01_health_and_assets(self):
  with urllib.request.urlopen(self.base+'/api/health') as r:self.assertTrue(json.load(r)['ok'])
  for path in ['/','/frontend/styles.css','/frontend/app.js']:
   with urllib.request.urlopen(self.base+path) as r:self.assertEqual(r.status,200)
 def test_02_write_protection(self):
  t=CONFIG['tools'][0]
  with self.assertRaises(urllib.error.HTTPError) as e:self.request(t['id'],t['actions'][0]['id'],headers={'Content-Type':'application/json'})
  self.assertEqual(e.exception.code,403)
  with self.assertRaises(urllib.error.HTTPError) as e:self.request(t['id'],t['actions'][0]['id'],headers={'Content-Type':'application/json','X-Project-Request':'1','Origin':'https://untrusted.example'})
  self.assertEqual(e.exception.code,403)
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(urllib.request.Request(self.base+'/api/health',headers={'Host':'untrusted.example'}))
  self.assertEqual(e.exception.code,403)
 def test_03_invalid_action(self):
  with self.assertRaises(urllib.error.HTTPError) as e:self.request('unknown','unknown')
  self.assertEqual(e.exception.code,400)
 def test_04_actual_backends(self):
  for t in CONFIG['tools']:
   k=t['kind'];id=t['id']
   with self.subTest(kind=k):
    if k=='redis':
     self.assertEqual(self.request(id,'command',{'command':'PING'})['output'],'PONG')
     self.request(id,'command',{'command':'SET ui_test value PX 150'})
     self.assertEqual(self.request(id,'command',{'command':'GET ui_test'})['output'],'value')
     self.assertEqual(len(self.request(id,'keys')['rows']),1)
     time.sleep(.2);self.assertIsNone(self.request(id,'command',{'command':'GET ui_test'})['output'])
     self.request(id,'command',{'command':'INCR counter'})
     self.assertEqual(self.request(id,'command',{'command':'GET counter'})['output'],'1')
    elif k=='database':
     self.request(id,'execute',{'sql':'CREATE TABLE tests (id INT, name TEXT);'})
     self.request(id,'execute',{'sql':"INSERT INTO tests VALUES (1, 'Ada');"})
     self.assertEqual(self.request(id,'execute',{'sql':'SELECT * FROM tests;'})['rows'],[[1,'Ada']])
     self.assertGreater(self.request(id,'stats')['rows'][0]['pages'],0)
     self.request(id,'execute',{'sql':'DELETE FROM tests WHERE id = 1;'})
     self.assertEqual(self.request(id,'execute',{'sql':'SELECT * FROM tests;'})['rows'],[])
    elif k=='agent':
     self.request(id,'chat',{'message':'My name is Ada'})
     self.assertIn('Ada',self.request(id,'chat',{'message':'What is my name?'})['output'])
     self.assertIn('Ada',self.request(id,'recall',{'message':'name'})['output'])
    elif k=='compiler':
     self.assertIn('main',self.request(id,'compile',{'source':'int main() { return 42; }'})['output'])
     self.assertGreater(len(self.request(id,'examples')['rows']),0)
    elif k=='search':
     self.assertGreater(len(self.request(id,'search',{'query':'B+ tree','mode':'offline'})['rows']),0)
     self.assertEqual(self.request(id,'search',{'query':'no-match-sentinel','mode':'offline'})['rows'],[])
    elif k=='snapshot':
     self.request(id,'init',{'blocks':'16'});self.request(id,'write',{'block':'0','content':'original'})
     self.request(id,'snapshot',{'name':'before'});self.request(id,'write',{'block':'0','content':'changed'})
     self.assertIn('changed',self.request(id,'read',{'block':'0'})['output'])
     self.request(id,'rollback',{'name':'before'})
     self.assertIn('original',self.request(id,'read',{'block':'0'})['output'])
     for action in ['stats','list','verify']:self.request(id,action)
    elif k=='expenses':
     r=self.request(id,'add',{'amount':'12.50','category':'Food','date':'2026-10-12','description':'Lunch'})
     self.assertEqual(r['rows'][0]['amount_cents'],1250)
     self.assertEqual(self.request(id,'reports')['rows'][0]['total_cents'],1250)
     self.assertEqual(len(self.request(id,'list',{'month':'2026-10'})['rows']),1)
     self.request(id,'delete',{'id':'1'});self.assertEqual(self.request(id,'list')['rows'],[])
     with self.assertRaises(urllib.error.HTTPError):self.request(id,'add',{'amount':'-1','category':'Food','date':'2026-10-12'})
    elif k=='school':
     before=self.request(id,'list')['rows'][0]['registered']
     self.request(id,'signup',{'activity':'Chess Club','email':'ui-test@example.com'})
     self.assertEqual(self.request(id,'list')['rows'][0]['registered'],before+1)
     with self.assertRaises(urllib.error.HTTPError):self.request(id,'signup',{'activity':'Chess Club','email':'ui-test@example.com'})
    elif k=='fitness':
     self.request(id,'add',{'name':'Ada','team':'Blue','activity':'Walking','minutes':'30','date':'2026-10-12'})
     self.assertEqual(self.request(id,'leaderboard')['rows'][0]['total_minutes'],30)
     self.assertEqual(len(self.request(id,'list')['rows']),1)
     self.request(id,'delete',{'id':'1'});self.assertEqual(self.request(id,'list')['rows'],[])
    elif k=='preview':
     rows=self.request(id,'list')['rows'];self.assertGreater(len(rows),0)
     for r in rows:
      preview=self.request(id,'open',{'page':r['path']})['preview']
      with urllib.request.urlopen(self.base+preview) as resp:self.assertTrue(resp.headers['Content-Type'].startswith('text/html'));self.assertGreater(len(resp.read()),0)
     with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(self.base+'/preview/README.md')
     with self.assertRaises(urllib.error.HTTPError):self.request(id,'open',{'page':'../README.md'})
 def test_05_static_path_isolation(self):
  for p in ['/frontend/../backend/server.py','/preview/.git/config','/preview/%2e%2e/README.md']:
   with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(self.base+p)
if __name__=='__main__':unittest.main()
