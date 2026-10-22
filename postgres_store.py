"""Explicit PostgreSQL storage foundation. Not selected by production workers yet.

Schema installation uses migration credentials; runtime never creates tables.
All records are scoped to a workspace/bot namespace. Uncertain sends remain fenced.
"""
import json
import psycopg
from psycopg.rows import dict_row


SCHEMA = '''
CREATE TABLE IF NOT EXISTS partner_agent.jobs (
 sequence BIGINT GENERATED ALWAYS AS IDENTITY,
 namespace TEXT NOT NULL, key TEXT NOT NULL, event_id TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 ref TEXT, result_url TEXT, reply TEXT,
 reply_state TEXT NOT NULL DEFAULT 'pending', error TEXT,
 PRIMARY KEY(namespace,key), UNIQUE(namespace,event_id)
);
CREATE TABLE IF NOT EXISTS partner_agent.outbox_delivery (
 namespace TEXT NOT NULL, id TEXT NOT NULL, snapshot TEXT NOT NULL,
 state TEXT NOT NULL, ts TEXT, decision TEXT, actor TEXT, at TEXT, error TEXT,
 PRIMARY KEY(namespace,id)
);
CREATE INDEX IF NOT EXISTS outbox_receipt ON partner_agent.outbox_delivery(namespace,ts);
CREATE TABLE IF NOT EXISTS partner_agent.outbox_events (
 sequence BIGINT GENERATED ALWAYS AS IDENTITY,
 namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
 done BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(namespace,key)
);
'''


class PostgresStore:
    def __init__(self, connection, namespace):
        if not namespace or len(namespace.split(':')) != 2 or not all(namespace.split(':')):
            raise ValueError('Use workspace:bot as the storage namespace')
        if connection.get('sslmode') != 'verify-full' or not connection.get('sslrootcert'):
            raise ValueError('PostgreSQL requires verify-full and a trusted CA file')
        self.connection = dict(connection)
        self.namespace = namespace

    def connect(self):
        return psycopg.connect(**self.connection, connect_timeout=10, row_factory=dict_row)

    def enqueue(self, event):
        with self.connect() as db:
            return db.execute('''INSERT INTO partner_agent.jobs(namespace,key,event_id,payload)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (self.namespace,event['key'],event['event_id'],json.dumps(event))).rowcount == 1

    def get(self, key):
        with self.connect() as db:
            return db.execute('SELECT * FROM partner_agent.jobs WHERE namespace=%s AND key=%s',
                              (self.namespace,key)).fetchone()

    def update(self, key, **fields):
        if not fields or not fields.keys() <= {'state','ref','result_url','reply','reply_state','error'}:
            raise ValueError('Invalid job fields')
        with self.connect() as db:
            db.execute('UPDATE partner_agent.jobs SET '+','.join(k+'=%s' for k in fields)+
                       ' WHERE namespace=%s AND key=%s',(*fields.values(),self.namespace,key))

    def claim_send(self, row, timestamp):
        # Context manager commits before the caller can send anything externally.
        with self.connect() as db:
            return db.execute('''INSERT INTO partner_agent.outbox_delivery
                (namespace,id,snapshot,state,at) VALUES (%s,%s,%s,'sending',%s)
                ON CONFLICT DO NOTHING''',
                (self.namespace,row['id'],json.dumps(row),timestamp)).rowcount == 1

    def receipt(self, row_id, ts):
        with self.connect() as db:
            return db.execute('''UPDATE partner_agent.outbox_delivery SET ts=%s,state='sync'
                WHERE namespace=%s AND id=%s AND state='sending' AND ts IS NULL''',
                (ts,self.namespace,row_id)).rowcount == 1

    def delivery(self, row_id):
        with self.connect() as db:
            return db.execute('SELECT * FROM partner_agent.outbox_delivery WHERE namespace=%s AND id=%s',
                              (self.namespace,row_id)).fetchone()

    def decide(self, row_id, decision, actor, timestamp):
        # Authorization remains the caller's responsibility; the DB enforces first-writer wins.
        with self.connect() as db:
            return db.execute('''UPDATE partner_agent.outbox_delivery
                SET decision=%s,actor=%s,at=%s,state='deciding'
                WHERE namespace=%s AND id=%s AND decision IS NULL AND ts IS NOT NULL''',
                (decision,actor,timestamp,self.namespace,row_id)).rowcount == 1

    def enqueue_event(self, event):
        with self.connect() as db:
            return db.execute('''INSERT INTO partner_agent.outbox_events(namespace,key,payload)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''',
                (self.namespace,event['key'],json.dumps(event))).rowcount == 1

    def owns(self, channel, ts):
        with self.connect() as db:
            rows=db.execute('SELECT snapshot FROM partner_agent.outbox_delivery WHERE namespace=%s AND ts=%s',
                            (self.namespace,ts)).fetchall()
            return any(json.loads(r['snapshot'])['channel']==channel for r in rows)

    def save_delivery(self, row_id, **fields):
        if not fields or not fields.keys() <= {'state','ts','decision','actor','at','error'}:
            raise ValueError('Invalid ledger field')
        with self.connect() as db:
            db.execute('UPDATE partner_agent.outbox_delivery SET '+','.join(k+'=%s' for k in fields)+
                       ' WHERE namespace=%s AND id=%s',(*fields.values(),self.namespace,row_id))

    def pending_deliveries(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM partner_agent.outbox_delivery WHERE namespace=%s AND state IN ('sync','deciding')",(self.namespace,)).fetchall()

    def matching_deliveries(self, ts):
        with self.connect() as db:
            return db.execute('SELECT * FROM partner_agent.outbox_delivery WHERE namespace=%s AND ts=%s',(self.namespace,ts)).fetchall()

    def pending_events(self):
        with self.connect() as db:
            return db.execute('SELECT * FROM partner_agent.outbox_events WHERE namespace=%s AND done=FALSE ORDER BY sequence LIMIT 100',(self.namespace,)).fetchall()

    def finish_event(self, key):
        with self.connect() as db:
            db.execute('UPDATE partner_agent.outbox_events SET done=TRUE WHERE namespace=%s AND key=%s',(self.namespace,key))
