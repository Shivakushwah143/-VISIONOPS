"""Durable bounded edge outbox and activation journal (SQLite WAL)."""
import sqlite3,json,time
from pathlib import Path
class State:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,created REAL); CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS deadletter(id TEXT PRIMARY KEY,error TEXT,created REAL);')
        self.db.commit()
    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone();return json.loads(row[0]) if row else default
    def put(self,key,value):
        with self.db:self.db.execute('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
    def put_many(self,values):
        with self.db:
            self.db.executemany('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',[(key,json.dumps(value)) for key,value in values.items()])
    def enqueue(self,event):
        kind=event['kind'];id=event['safety_event_id'] if kind=='safety' else event['detection_event_id'];payload=json.dumps(event,separators=(',',':'))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?,?)',(id,kind,payload,time.time()))
            expired=self.db.execute('DELETE FROM outbox WHERE created<?',(time.time()-7*86400,)).rowcount
            if expired:self.put('lost_events',self.get('lost_events',0)+expired)
            cap,bytecap=(10000,100*1024**2) if kind=='safety' else (1000,10*1024**2)
            while True:
                n,b=self.db.execute('SELECT count(*),coalesce(sum(length(payload)),0) FROM outbox WHERE kind=?',(kind,)).fetchone()
                if n<=cap and b<=bytecap:break
                self.db.execute('DELETE FROM outbox WHERE id=(SELECT id FROM outbox WHERE kind=? ORDER BY created LIMIT 1)',(kind,));self.put('lost_events',self.get('lost_events',0)+1)
    def batch(self):
        rows=self.db.execute("SELECT payload FROM outbox ORDER BY CASE kind WHEN 'safety' THEN 0 ELSE 1 END,created LIMIT 100").fetchall();result=[];size=0
        for (payload,) in rows:
            if size+len(payload)>1000000:break
            result.append(json.loads(payload));size+=len(payload)
        return result
    def ack(self,results):
        with self.db:
            for r in results:
                if r['status'] in ('accepted','duplicate','rejected'):
                    if r['status']=='rejected':self.db.execute('INSERT OR REPLACE INTO deadletter VALUES(?,?,?)',(r['event_id'],r.get('error_code','rejected'),time.time()))
                    self.db.execute('DELETE FROM outbox WHERE id=?',(r['event_id'],))
            self.db.execute('DELETE FROM deadletter WHERE id NOT IN (SELECT id FROM deadletter ORDER BY created DESC LIMIT 1000)')
    def depth(self):return self.db.execute('SELECT count(*) FROM outbox').fetchone()[0]
