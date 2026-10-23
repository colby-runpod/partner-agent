import os
import unittest
from unittest.mock import patch
import fresh_start as f
import storage

CUT='2026-10-23T16:00:00Z'
class FreshStartTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,POSTGRES_FRESH_START_AT=CUT)
        self.env.start();self.addCleanup(self.env.stop)
        self.ts=f.cutoff().timestamp()
    def event(self,**values): return {'event':dict(ts=str(self.ts+1),**values)}
    def test_new_event_and_new_thread_allowed(self):
        self.assertTrue(f.allows_event(self.event(thread_ts=str(self.ts+0.5))))
    def test_old_equal_missing_invalid_nonfinite_rejected(self):
        for v in (str(self.ts-1),str(self.ts),None,'bad','NaN','Infinity'):
            self.assertFalse(f.allows_event({'event':{'ts':v}}))
    def test_new_delivery_of_old_message_or_thread_rejected(self):
        self.assertFalse(f.allows_event(self.event(thread_ts=str(self.ts-1))))
        self.assertFalse(f.allows_event(self.event(item={'ts':str(self.ts-1)})))
        self.assertFalse(f.allows_event(self.event(message={'ts':str(self.ts-1)})))
    def test_current_reaction_to_new_message_allowed(self):
        self.assertTrue(f.allows_event({'event':{'event_ts':str(self.ts+2),'item':{'ts':str(self.ts+1)}}}))
    def test_buttons_require_new_message_and_click(self):
        p={'message':{'ts':str(self.ts+1)},'actions':[{'action_ts':str(self.ts+2)}]}
        self.assertTrue(f.allows_action(p))
        p['message']['ts']=str(self.ts-1)
        self.assertFalse(f.allows_action(p))
        self.assertFalse(f.allows_action({}))
    def test_invalid_cutoff_fails(self):
        for v in ('','2026-10-23','2026-10-23T16:00:00'):
            with patch.dict(os.environ,POSTGRES_FRESH_START_AT=v):
                with self.assertRaises(ValueError):f.cutoff()
    def test_reconciled_assertion_cannot_mix_with_fresh_start(self):
        with patch.dict(os.environ,POSTGRES_STATE_READY='true'):
            with self.assertRaisesRegex(ValueError,'not both'):storage.postgres_settings()
    def test_legacy_shutdown_required(self):
        with patch.dict(os.environ,POSTGRES_STATE_READY='false',LEGACY_WRITERS_STOPPED='false'):
            with self.assertRaisesRegex(ValueError,'shutdown'):storage.postgres_settings()
    def test_cutoffs_must_match(self):
        with patch.dict(os.environ,POSTGRES_STATE_READY='false',LEGACY_WRITERS_STOPPED='true',OUTBOX_CREATED_AFTER='2026-10-22T16:00:00Z'):
            with self.assertRaisesRegex(ValueError,'match'):storage.postgres_settings()

    def test_signed_old_event_acknowledged_without_enqueue(self):
        import io,json,time,hmac,hashlib
        from unittest.mock import Mock
        from intake import Application
        from test_intake import config,event
        cfg=config(); store=Mock(); body=event()
        body['type']='event_callback'
        body['event']['ts']=str(self.ts-1)
        raw=json.dumps(body).encode();stamp=str(int(time.time()))
        signature='v0='+hmac.new(cfg['secret'].encode(),b'v0:'+stamp.encode()+b':'+raw,hashlib.sha256).hexdigest()
        env={'PATH_INFO':'/slack/events','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(raw)),
             'wsgi.input':io.BytesIO(raw),'HTTP_X_SLACK_REQUEST_TIMESTAMP':stamp,'HTTP_X_SLACK_SIGNATURE':signature}
        statuses=[]
        Application(cfg,store)(env,lambda status,headers:statuses.append(status))
        self.assertEqual(statuses,['200 OK']);store.enqueue.assert_not_called()
