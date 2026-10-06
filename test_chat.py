import copy
import io
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import chat
from intake import Store, Application

CFG = dict(team_id='T1', app_id='A1', secret='secret', enabled=True, channels={'C1'}, users={'U1'})
BODY = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='Ev1', event=dict(type='app_mention', channel='C1', user='U1', ts='12.2', thread_ts='12.1', text='<@UBOT> summarize'))

class Fake:
    def __init__(self): self.sent=[]; self.calls=0
    def thread(self, e): return 'Company: Example'
    def answer(self, e, c): self.calls+=1; return 'A proposed partnership.'
    def reply(self, e, text): self.sent.append((e['thread_ts'], text))

class ChatTests(unittest.TestCase):
    def test_filters(self):
        self.assertEqual(chat.accept(BODY, CFG)['thread_ts'], '12.1')
        for field, value in [('type','message'), ('user','UOTHER'), ('channel','COTHER'), ('bot_id','B1'), ('subtype','message_changed')]:
            b=copy.deepcopy(BODY); b['event'][field]=value
            self.assertIsNone(chat.accept(b, CFG))
        b=copy.deepcopy(BODY); b['team_id']='TOTHER'
        self.assertIsNone(chat.accept(b, CFG))
        b=copy.deepcopy(BODY); b['api_app_id']='AOTHER'
        self.assertIsNone(chat.accept(b, CFG))

    def test_signed_queue_and_duplicate_reply(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/queue.db'); app=Application(CFG, store, chat.accept)
            raw=json.dumps(BODY).encode(); ts=str(int(time.time()))
            sig='v0='+hmac.new(b'secret', b'v0:'+ts.encode()+b':'+raw, hashlib.sha256).hexdigest()
            def request(signature):
                codes=[]
                app(dict(PATH_INFO='/slack/events', REQUEST_METHOD='POST', CONTENT_LENGTH=str(len(raw)), HTTP_X_SLACK_REQUEST_TIMESTAMP=ts, HTTP_X_SLACK_SIGNATURE=signature, **{'wsgi.input':io.BytesIO(raw)}), lambda code,headers:codes.append(code))
                return codes[0]
            self.assertEqual(request('invalid'), '401 Unauthorized')
            self.assertIsNone(store.next_job())
            self.assertEqual(request(sig), '200 OK'); self.assertEqual(request(sig), '200 OK')
            client=Fake(); job=store.next_job()
            chat.process(store,job,client); chat.process(store,store.get(job['key']),client)
            self.assertEqual(client.sent, [('12.1','A proposed partnership.')]); self.assertEqual(client.calls,1)
            self.assertIsNone(store.next_job())

    def test_thread_error_does_not_hallucinate(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/q');store.enqueue(chat.accept(BODY,CFG));client=Fake()
            client.thread=lambda e: (_ for _ in ()).throw(ValueError())
            chat.process(store,store.next_job(),client)
            self.assertEqual(client.calls,0);self.assertIn('could not read',client.sent[0][1])

    def test_ambiguous_send_not_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/q');e=chat.accept(BODY,CFG);store.enqueue(e);client=Fake()
            def fail(e,t): client.sent.append(t);raise TimeoutError()
            client.reply=fail
            chat.process(store,store.next_job(),client)
            chat.process(store,store.get(e['key']),client)
            self.assertEqual(len(client.sent),1);self.assertEqual(store.get(e['key'])['reply_state'],'review')

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN':'test'})
    @patch('chat.api')
    def test_thread_pagination_and_attachments(self, api):
        api.side_effect=[{'messages':[{'ts':'12.1','attachments':[{'text':'proposal'}]}], 'response_metadata':{'next_cursor':'more'}}, {'messages':[{'ts':'12.2','text':'question'}]}]
        context=chat.ChatClients().thread(chat.accept(BODY,CFG))
        self.assertIn('proposal',context);self.assertIn('question',context)
        self.assertIn('cursor=more',api.call_args.args[0])

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN':'test'})
    @patch('chat.api')
    def test_only_requester_can_be_mentioned(self, api):
        chat.ChatClients().reply(chat.accept(BODY,CFG),'<!channel> <@UEVIL>')
        text=api.call_args.args[2]['text']
        self.assertTrue(text.startswith('<@U1>'));self.assertNotIn('<!channel>',text);self.assertNotIn('<@UEVIL>',text)

    @patch.dict('os.environ', {'OPENAI_API_KEY':'test', 'OPENAI_MODEL':'test'})
    @patch('chat.api')
    def test_model_response(self, api):
        api.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Summary'}]}]}
        self.assertEqual(chat.ChatClients().answer(chat.accept(BODY,CFG),'evidence'),'Summary')
        self.assertFalse(api.call_args.args[2]['store'])
        self.assertNotIn('tools',api.call_args.args[2])
        api.return_value={'status':'incomplete'}
        with self.assertRaises(ValueError): chat.ChatClients().answer(chat.accept(BODY,CFG),'evidence')

if __name__=='__main__': unittest.main()
