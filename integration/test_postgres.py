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
