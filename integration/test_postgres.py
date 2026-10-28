"""Run against a disposable TLS-enabled PostgreSQL, never a production database."""
import concurrent.futures
import json
import os
import unittest
import uuid
import psycopg
from postgres_store import PostgresStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('POSTGRES_DISPOSABLE_TEST') != 'true':
            raise RuntimeError('Disposable database test opt-in required')
        credential=json.loads(os.environ['RUNTIME_CREDENTIAL'])
        self.connection=dict(host=os.environ['DB_HOST'],dbname='partneragent',
            user=credential['username'],password=credential['password'],
            sslmode='verify-full',sslrootcert='/app/rds-ca.pem')
        self.store=PostgresStore(self.connection,'test:'+uuid.uuid4().hex)

    def test_fresh_boundary_persists_and_cannot_change_or_disappear(self):
        from unittest.mock import patch
        import fresh_start
        with patch.dict(os.environ,POSTGRES_FRESH_START_AT='2026-10-23T16:00:00Z'):
            self.parallel(lambda:fresh_start.pin_boundary(self.store))
            fresh_start.pin_boundary(self.store)
        with patch.dict(os.environ,POSTGRES_FRESH_START_AT='2026-10-24T16:00:00Z'):
            with self.assertRaises(ValueError):fresh_start.pin_boundary(self.store)
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):fresh_start.pin_boundary(self.store)

    def test_fresh_boundary_refuses_existing_jobs(self):
        from unittest.mock import patch
        import fresh_start
        self.store.enqueue({'key':'existing','event_id':'existing'})
        with patch.dict(os.environ,POSTGRES_FRESH_START_AT='2026-10-23T16:00:00Z'):
            with self.assertRaises(ValueError):fresh_start.pin_boundary(self.store)

    def parallel(self, fn):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(lambda _:fn(),range(8)))

    def test_concurrent_enqueue(self):
        event={'key':'job','event_id':'event','text':'fixture'}
        self.assertEqual(sum(self.parallel(lambda:self.store.enqueue(event))),1)
        self.assertEqual(json.loads(self.store.get('job')['payload']),event)

    def test_send_fence_survives_reconnect(self):
        row={'id':'row','channel':'test'}
        self.assertEqual(sum(self.parallel(lambda:self.store.claim_send(row,'now'))),1)
        reconnected=PostgresStore(self.connection,self.store.namespace)
        self.assertFalse(reconnected.claim_send(row,'later'))
        self.assertEqual(reconnected.delivery('row')['state'],'sending')
        self.assertTrue(reconnected.receipt('row','1.2'))
        self.assertFalse(reconnected.receipt('row','3.4'))
        self.assertTrue(reconnected.owns('test','1.2'))
        self.assertFalse(reconnected.owns('other','1.2'))

    def test_first_decision_wins(self):
        self.store.claim_send({'id':'row'},'now')
        self.assertFalse(self.store.decide('row','Approve','colby','now'))
        self.store.receipt('row','1.2')
        self.assertEqual(sum(self.parallel(lambda:self.store.decide('row','Approve','colby','now'))),1)
        self.assertFalse(self.store.decide('row','Decline','colby','later'))
        self.assertEqual(self.store.delivery('row')['decision'],'Approve')

    def test_namespace_isolation(self):
        self.store.claim_send({'id':'row','channel':'test'},'now')
        other=PostgresStore(self.connection,'test:other')
        self.assertIsNone(other.delivery('row'))
        self.assertTrue(other.claim_send({'id':'row'},'now'))

    def test_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.store.connect() as db:
                db.execute('INSERT INTO partner_agent.jobs(namespace,key,event_id,payload) VALUES (%s,%s,%s,%s)',
                           (self.store.namespace,'rollback','event','{}'))
                raise RuntimeError('simulate failure')
        self.assertIsNone(self.store.get('rollback'))

    def test_permissions(self):
        for query in ['CREATE TABLE partner_agent.forbidden(id int)',
                      'DELETE FROM partner_agent.jobs', 'DROP TABLE partner_agent.jobs']:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with self.store.connect() as db: db.execute(query)

    def test_duplicate_events(self):
        self.assertEqual(sum(self.parallel(lambda:self.store.enqueue_event({'key':'event'}))),1)

    def test_weekly_feedback_progress_survives_postgres_restart(self):
        from unittest.mock import patch
        from outbox import Outbox
        from test_weekly_outbox import weekly
        from test_weekly_feedback import FeedbackRemote, DRAFT
        import weekly_feedback as w
        p=weekly(); remote=FeedbackRemote([p])
        with patch.dict(os.environ, SLACK_TEAM_ID='T1', WEEKLY_OUTBOX_ENABLED='true', WEEKLY_FEEDBACK_ENABLED='true'):
            box=Outbox(None, remote, storage=self.store); box.post(p)
            event={'kind':'weekly_feedback','key':'weekly-fixture','event_id':'weekly-fixture',
                   'channel':w.o.WEEKLY_CHANNEL,'message_ts':box.get(p['id'])['ts'],
                   'ts':'1793133459.4','user':w.o.COLBY,'text':'<@'+w.o.BOT+'> Test update','progress':{}}
            box.enqueue_event(event); remote.fail_destination=DRAFT; remote.accept_timeout=True
            box.poll()
            self.assertEqual(json.loads(self.store.pending_events()[0]['payload'])['progress']['draft'],'writing')
            other=PostgresStore(self.connection, 'test:other-weekly')
            other.enqueue_event(dict(event, text='Other namespace'))
            remote.fail_destination=None
            Outbox(None, remote, storage=PostgresStore(self.connection,self.store.namespace)).poll()
            self.assertEqual(self.store.pending_events(),[])
            self.assertEqual(json.loads(other.pending_events()[0]['payload'])['text'],'Other namespace')
            self.assertEqual(remote.append_count[DRAFT],1)

    def test_outbox_restart_finishes_writeback_without_repost(self):
        from unittest.mock import patch
        from outbox import Outbox, prop
        from test_outbox import Remote, page
        p=page(); remote=Remote([p]); remote.patch_fail=1
        with patch.dict(os.environ,SLACK_TEAM_ID='T1',OUTBOX_TEST_IDS=p['id']):
            box=Outbox(None,remote,storage=self.store)
            box.poll()
            self.assertEqual(self.store.delivery(p['id'])['state'],'sync')
            restarted=Outbox(None,remote,storage=PostgresStore(self.connection,self.store.namespace))
            restarted.poll(); restarted.poll()
        self.assertEqual(sum(m=='chat.postMessage' for m,_ in remote.calls),1)
        self.assertEqual(prop(remote.get(p['id']),'Status'),'Posted')

    def test_outbox_timeout_is_not_reposted_after_restart(self):
        from unittest.mock import patch
        from outbox import Outbox
        from test_outbox import Remote, page
        p=page(); remote=Remote([p]); remote.fail=[TimeoutError()]
        with patch.dict(os.environ,SLACK_TEAM_ID='T1',OUTBOX_TEST_IDS=p['id']):
            Outbox(None,remote,storage=self.store).poll()
            Outbox(None,remote,storage=PostgresStore(self.connection,self.store.namespace)).poll()
        self.assertEqual(sum(m=='chat.postMessage' for m,_ in remote.calls),1)
        self.assertEqual(self.store.delivery(p['id'])['state'],'sending')

    def test_pending_events_scoped_and_persistent(self):
        self.store.enqueue_event({'key':'one'})
        other=PostgresStore(self.connection,'test:'+uuid.uuid4().hex)
        self.assertEqual(other.pending_events(),[])
        other.finish_event('one')
        self.assertEqual(len(self.store.pending_events()),1)
        self.store.finish_event('one')
        self.assertEqual(self.store.pending_events(),[])

    def test_receiver_commits_before_ack_and_deduplicates_retry(self):
        import hashlib, hmac, io, time
        from intake import Application
        raw=b'{"type":"event_callback"}'
        stamp=str(int(time.time()))
        signature='v0='+hmac.new(b'fixture',b'v0:'+stamp.encode()+b':'+raw,hashlib.sha256).hexdigest()
        event={'key':'incoming','event_id':'incoming-event'}
        def invoke(store):
            status=[]
            app=Application({'secret':'fixture','enabled':True},store,lambda *_:event)
            app({'PATH_INFO':'/slack/events','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(raw)),
                 'wsgi.input':io.BytesIO(raw),'HTTP_X_SLACK_REQUEST_TIMESTAMP':stamp,
                 'HTTP_X_SLACK_SIGNATURE':signature},lambda code,_:status.append(code))
            return status[0]
        self.assertEqual(invoke(self.store),'200 OK')
        restarted=PostgresStore(self.connection,self.store.namespace)
        self.assertEqual(restarted.next_job()['key'],'incoming')
        self.assertEqual(invoke(restarted),'200 OK')
        restarted.update('incoming',state='done')
        self.assertIsNone(restarted.next_job())
        broken=PostgresStore(dict(self.connection,port=1),self.store.namespace)
        self.assertEqual(invoke(broken),'503 Service Unavailable')

    def test_commit_acknowledgement_failure_is_retryable(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from storage_errors import StorageUnavailable
        connect=self.store.connect
        @contextmanager
        def uncertain_commit():
            with connect() as db:
                yield db
            # Simulate loss of the commit acknowledgement after the server committed.
            raise psycopg.OperationalError('simulated commit acknowledgement loss')
        event={'key':'uncertain','event_id':'uncertain-event'}
        with patch.object(self.store,'connect',uncertain_commit):
            with self.assertRaises(StorageUnavailable): self.store.enqueue(event)
        self.assertFalse(self.store.enqueue(event))
        self.assertEqual(self.store.next_job()['key'],'uncertain')

    def test_review_history_survives_restart_and_scopes_channels(self):
        from postgres_ledger import PostgresLedger
        ledger=PostgresLedger(self.store)
        ledger.record('one',{'channel':'C1','thread_ts':'1.0'},
                      {'company':'A &amp; B','score':3,'headline':'Review','drafts':[('a','b')]},'ok',now=1)
        ledger.delivered('one','sent','2.0')
        ledger.mark('one',notes='retained')
        ledger.put('cursor',123)
        restarted=PostgresLedger(PostgresStore(self.connection,self.store.namespace))
        self.assertEqual(restarted.review('one')['notes'],'retained')
        self.assertEqual(restarted.get('cursor'),'123')
        self.assertEqual(restarted.find(card_ts='2.0',channel='C1')['company'],'A & B')
        self.assertIsNone(restarted.find(card_ts='2.0',channel='C2'))
        other=PostgresLedger(PostgresStore(self.connection,'test:'+uuid.uuid4().hex))
        self.assertIsNone(other.review('one'))
        self.assertEqual(other.since(0),[])
        self.assertEqual(other.get('cursor','missing'),'missing')

    def test_concurrent_feedback_has_one_active_vote(self):
        from postgres_ledger import PostgresLedger
        ledger=PostgresLedger(self.store)
        ledger.record('one',{'channel':'C1','thread_ts':'1.0'},{'company':'Fixture'},'ok',now=1)
        self.parallel(lambda:ledger.feedback(uuid.uuid4().hex,'one','U1','up',now=2))
        self.assertEqual(len(ledger.feedback_since(0)),1)
        ledger.feedback('down','one','U1','down',now=3)
        self.assertEqual([x['vote'] for x in ledger.feedback_since(0)],['down'])
        ledger.unvote('one','U1','down')
        self.assertEqual(ledger.feedback_since(0),[])

    def test_worker_exclusion_and_clean_takeover(self):
        from postgres_worker import worker_session, WorkerBusy, WorkerStopped
        with worker_session(self.store,'queue') as worker:
            worker.enqueue({'key':'owned','event_id':'owned'})
            with self.assertRaises(WorkerBusy):
                with worker_session(self.store,'queue'): pass
            with worker_session(self.store,'outbox') as independent:
                independent.check()
        with self.assertRaises(WorkerStopped): worker.check()
        with worker_session(self.store,'queue') as replacement:
            self.assertEqual(replacement.next_job()['key'],'owned')

    def test_worker_crash_blocks_automatic_takeover(self):
        from postgres_worker import worker_session, WorkerBusy
        with self.assertRaises(RuntimeError):
            with worker_session(self.store,'queue'):
                raise RuntimeError('simulated process failure')
        with self.assertRaises(WorkerBusy):
            with worker_session(self.store,'queue'): pass

    def test_worker_connection_loss_is_fatal_and_retains_hold(self):
        from postgres_worker import worker_session, WorkerBusy, WorkerStopped
        with self.assertRaises(WorkerStopped):
            with worker_session(self.store,'queue') as worker:
                worker._db.close()
                worker.next_job()
        with self.assertRaises(WorkerBusy):
            with worker_session(self.store,'queue'): pass

    def test_worker_namespace_independence(self):
        from postgres_worker import worker_session
        other=PostgresStore(self.connection,'test:'+uuid.uuid4().hex)
        with worker_session(self.store,'queue') as first:
            with worker_session(other,'queue') as second:
                first.check(); second.check()

    def test_configured_startup_uses_shared_store_through_workers(self):
        from unittest.mock import patch
        import storage
        from test_outbox import Remote, page
        team='T'+uuid.uuid4().hex
        p=page()
        with patch.dict(os.environ,STORAGE_BACKEND='postgres',POSTGRES_STATE_READY='true',
                        SLACK_TEAM_ID=team,DB_SSL_ROOT_CERT='/app/rds-ca.pem',OUTBOX_TEST_IDS=p['id']):
            receiver=storage.make_store('/must-not-create-sqlite')
            receiver.enqueue({'key':'startup','event_id':'startup'})
            with storage.worker_store('/must-not-create-sqlite') as worker:
                self.assertEqual(worker.next_job()['key'],'startup')
                ledger=storage.make_ledger(worker)
                ledger.record('startup',{'channel':'C1','thread_ts':'1.0'},{'company':'Fixture'},'ok')
                worker.update('startup',state='done')
            with storage.worker_store('/must-not-create-sqlite','outbox') as worker:
                box=storage.make_outbox(worker)
                remote=Remote([p])
                original=remote.slack
                def slack(method,data):
                    result=original(method,data)
                    if method=='auth.test': result['team_id']=team
                    return result
                remote.slack=slack
                box.remote=remote
                box.poll(); box.poll()
                self.assertEqual(sum(m=='chat.postMessage' for m,_ in remote.calls),1)
                receipt=worker.delivery(p['id'])
                self.assertTrue(storage.owns_message('C0CAL8C08RK',receipt['ts']))
            self.assertEqual(storage.make_ledger(receiver).review('startup')['company'],'Fixture')
            self.assertIsNone(receiver.next_job())

    def test_no_unverified_tls(self):
        settings=dict(self.connection,sslmode='require')
        with self.assertRaises(ValueError):PostgresStore(settings,'test:bot')

    def test_database_failure_propagates(self):
        settings=dict(self.connection,port=1)
        broken=PostgresStore(settings,self.store.namespace)
        with self.assertRaises(psycopg.OperationalError):
            broken.claim_send({'id':'row'},'now')


if __name__=='__main__':
    unittest.main(verbosity=2)
