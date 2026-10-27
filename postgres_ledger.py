"""Review history adapter. Runtime DML only; schemas installed by migration role."""
import html
import json
import time


class PostgresLedger:
    def __init__(self, store):
        self.store = store
        self.namespace = store.namespace

    def record(self, key, event, review, outcome, now=None):
        r = review or {}
        keep = None
        if review:
            keep = {k:r.get(k) for k in ('company','company_short','contact','first_name','email','submitted',
                'partnership_type','route','score','decision','summary','why','next_step','headline','screening','region','dean_motion','event_motion','signals')}
            keep['source_event'] = dict(event)
            keep.update(checks=r.get('checks',{}), drafts=[list(d) for d in r.get('drafts',[])],
                        blocker=(r.get('signals') or {}).get('blocker',''))
        with self.store.connect() as db:
            db.execute('''INSERT INTO partner_agent.reviews
                (namespace,key,channel,ts,created,company,route,score,decision,headline,outcome,delivered,data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)
                ON CONFLICT(namespace,key) DO UPDATE SET channel=EXCLUDED.channel,ts=EXCLUDED.ts,
                created=EXCLUDED.created,company=EXCLUDED.company,route=EXCLUDED.route,score=EXCLUDED.score,
                decision=EXCLUDED.decision,headline=EXCLUDED.headline,outcome=EXCLUDED.outcome,
                delivered='pending',data=EXCLUDED.data,card_ts=NULL,card_text=NULL,notion_url=NULL,
                notion_id=NULL,notes=NULL,parked_by=NULL''',
                (self.namespace,key,event['channel'],event['thread_ts'],time.time() if now is None else now,
                 html.unescape(r.get('company','')),r.get('route',''),str(r.get('score','')),
                 r.get('decision',''),html.unescape(r.get('headline','')),outcome,
                 json.dumps(keep) if keep is not None else None))

    def delivered(self,key,state,card_ts=None):
        with self.store.connect() as db:
            db.execute('UPDATE partner_agent.reviews SET delivered=%s,card_ts=COALESCE(%s,card_ts) WHERE namespace=%s AND key=%s',
                       (state,card_ts,self.namespace,key))

    def review(self,key):
        with self.store.connect() as db:
            return db.execute('SELECT * FROM partner_agent.reviews WHERE namespace=%s AND key=%s',(self.namespace,key)).fetchone()

    def mark(self,key,**fields):
        if not fields or not fields.keys() <= {'card_text','notion_url','notion_id','notes','parked_by'}:
            raise ValueError('Invalid ledger field')
        with self.store.connect() as db:
            db.execute('UPDATE partner_agent.reviews SET '+','.join(k+'=%s' for k in fields)+
                       ' WHERE namespace=%s AND key=%s',(*fields.values(),self.namespace,key))

    def find(self,card_ts=None,channel=None,thread_ts=None):
        # Channel is mandatory: Slack timestamps alone are not globally unique.
        if not channel: return None
        column,value=('card_ts',card_ts) if card_ts else ('ts',thread_ts)
        with self.store.connect() as db:
            return db.execute("SELECT * FROM partner_agent.reviews WHERE namespace=%s AND channel=%s AND "+column+
                "=%s AND outcome='ok' ORDER BY created DESC,key LIMIT 1",(self.namespace,channel,value)).fetchone()

    def feedback(self,fid,review_key,user,vote,value='',now=None):
        with self.store.connect() as db:
            # Serialize votes for this card, including the first vote. No DELETE privilege needed.
            parent=db.execute('SELECT key FROM partner_agent.reviews WHERE namespace=%s AND key=%s FOR UPDATE',
                              (self.namespace,review_key)).fetchone()
            if not parent: raise ValueError('Unknown review')
            if vote in ('up','down'):
                db.execute('''UPDATE partner_agent.feedback SET active=FALSE WHERE namespace=%s AND review_key=%s
                    AND "user"=%s AND vote IN ('up','down')''',(self.namespace,review_key,user))
            db.execute('''INSERT INTO partner_agent.feedback(namespace,id,review_key,"user",vote,value,created)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(namespace,id) DO UPDATE SET
                review_key=EXCLUDED.review_key,"user"=EXCLUDED."user",vote=EXCLUDED.vote,
                value=EXCLUDED.value,created=EXCLUDED.created,active=TRUE''',
                (self.namespace,fid,review_key,user,vote,value,time.time() if now is None else now))

    def unvote(self,review_key,user,vote):
        with self.store.connect() as db:
            db.execute('SELECT key FROM partner_agent.reviews WHERE namespace=%s AND key=%s FOR UPDATE',
                       (self.namespace,review_key))
            db.execute('UPDATE partner_agent.feedback SET active=FALSE WHERE namespace=%s AND review_key=%s AND "user"=%s AND vote=%s',
                       (self.namespace,review_key,user,vote))

    def feedback_since(self,t):
        with self.store.connect() as db:
            return db.execute('''SELECT f.*,r.company,r.decision,r.channel,r.ts FROM partner_agent.feedback f
                JOIN partner_agent.reviews r ON r.namespace=f.namespace AND r.key=f.review_key
                WHERE f.namespace=%s AND f.active=TRUE AND f.created>=%s ORDER BY f.created,f.id''',(self.namespace,t)).fetchall()

    def since(self,t):
        with self.store.connect() as db:
            return db.execute('SELECT * FROM partner_agent.reviews WHERE namespace=%s AND created>=%s ORDER BY created,key',
                              (self.namespace,t)).fetchall()

    def get(self,k,default=None):
        with self.store.connect() as db:
            row=db.execute('SELECT v FROM partner_agent.review_meta WHERE namespace=%s AND k=%s',(self.namespace,k)).fetchone()
            return row['v'] if row else default

    def put(self,k,v):
        with self.store.connect() as db:
            db.execute('INSERT INTO partner_agent.review_meta(namespace,k,v) VALUES (%s,%s,%s) ON CONFLICT(namespace,k) DO UPDATE SET v=EXCLUDED.v',
                       (self.namespace,k,str(v)))
