import base64
import tempfile
import unittest
from unittest.mock import Mock
from intake import Store
import gmail_intake as gmail

class Meta:
    def __init__(self): self.d={}
    def get(self,k): return self.d.get(k)
    def put(self,k,v): self.d[k]=v

class Fake:
    def __init__(self): self.history={'historyId':'12','history':[{'labelsAdded':[{'message':{'id':'abc'},'labelIds':['L1']}]}]}
    def get(self,path,params=None):
        if path=='profile': return {'emailAddress':'colby.henderson@runpod.io','historyId':'10'}
        if path=='labels': return {'labels':[{'name':'Partner Inbound','id':'L1'}]}
        if path=='history': return self.history
        if path=='messages/abc': return dict(id='abc',threadId='def',internalDate='1700000000000',labelIds=['L1'],payload=dict(mimeType='text/plain',body={'data':base64.urlsafe_b64encode(b'Partner request').decode()},headers=[{'name':'From','value':'Example <person@example.com>'},{'name':'Subject','value':'Partner request'}]))
        raise AssertionError(path)

CFG=dict(gmail_enabled=True,shadow_channel='COUT')
class GmailTests(unittest.TestCase):
    def test_baseline_skips_existing_then_label_event_enqueues_once(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/db');meta=Meta();client=Fake()
            self.assertEqual(gmail.poll(store,meta,client,CFG),0)
            self.assertEqual(gmail.poll(store,meta,client,CFG),1)
            self.assertEqual(gmail.poll(store,meta,client,CFG),0)
            row=store.next_job();self.assertIn('gmail:',row['key'])
            self.assertEqual(list(meta.d.values()),['12'])
    def test_failed_enqueue_does_not_advance_history(self):
        meta=Meta();client=Fake();store=Mock()
        gmail.poll(store,meta,client,CFG)
        store.enqueue.side_effect=RuntimeError('storage failed')
        with self.assertRaises(RuntimeError):gmail.poll(store,meta,client,CFG)
        self.assertEqual(list(meta.d.values()),['10'])
    def test_expired_history_fails_closed(self):
        meta=Meta();client=Fake();store=Mock();gmail.poll(store,meta,client,CFG)
        original=client.get
        client.get=lambda p,args=None: (_ for _ in ()).throw(ValueError('Gmail API HTTP 404')) if p=='history' else original(p,args)
        with self.assertRaises(ValueError):gmail.poll(store,meta,client,CFG)
        self.assertEqual(list(meta.d.values()),['10']);store.enqueue.assert_not_called()
    def test_wrong_account_rejected(self):
        with self.assertRaises(ValueError):gmail.poll(Mock(),Meta(),Fake(),dict(CFG,gmail_account='other@example.com'))
    def test_html_and_attachments(self):
        encoded=base64.urlsafe_b64encode(b'<p>Real request</p><script>bad()</script>').decode()
        self.assertEqual(gmail.body_text({'mimeType':'text/html','body':{'data':encoded}}),'Real request')
        self.assertEqual(gmail.body_text({'mimeType':'text/plain','filename':'private.txt','body':{'data':encoded}}),'')
