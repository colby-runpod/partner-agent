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

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':'test-secret', 'ANTHROPIC_MODEL':'claude-test'})
    @patch('chat.urllib.request.urlopen')
    def test_model_response(self, urlopen):
        def response(data):
            urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(data).encode())
        response({'stop_reason':'end_turn','content':[{'type':'thinking','thinking':'hidden'}, {'type':'text','text':'Summary'}]})
        self.assertEqual(chat.ChatClients().answer(chat.accept(BODY,CFG),'evidence'),'Summary')
        request=urlopen.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.anthropic.com/v1/messages')
        headers={k.lower():v for k,v in request.header_items()}
        self.assertEqual(headers['x-api-key'],'test-secret')
        self.assertEqual(headers['anthropic-version'],'2023-06-01')
        payload=json.loads(request.data)
        self.assertEqual(payload['model'],'claude-test')
        self.assertEqual(payload['system'],chat.INSTRUCTIONS)
        self.assertNotIn('tools',payload)
        response({'stop_reason':'max_tokens','content':[{'type':'text','text':'Partial'}]})
        with self.assertRaises(ValueError): chat.ChatClients().answer(chat.accept(BODY,CFG),'evidence')

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':'secret', 'ANTHROPIC_MODEL':'test'})
    @patch('chat.urllib.request.urlopen', side_effect=TimeoutError('private detail'))
    def test_claude_timeout_sanitized(self, urlopen):
        with self.assertRaisesRegex(ValueError, '^Claude response unavailable$'):
            chat.ChatClients().answer(chat.accept(BODY,CFG),'evidence')

    @patch.dict('os.environ', {'CHAT_ENABLED':'true', 'SLACK_BOT_TOKEN':'test', 'ANTHROPIC_API_KEY':'test', 'ANTHROPIC_MODEL':'test'}, clear=True)
    @patch('chat.load_config', return_value={})
    def test_claude_configuration_without_openai(self, cfg):
        self.assertTrue(chat.config()['enabled'])
        with patch.dict('os.environ', {'ANTHROPIC_MODEL':''}):
            with self.assertRaisesRegex(ValueError,'ANTHROPIC_MODEL'): chat.config()

if __name__=='__main__': unittest.main()
