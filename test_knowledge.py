import copy
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import knowledge
import partner_sources
from intake import Store

SOURCES=[dict(title='Partner FAQs',url='https://app.notion.com/p/'+list(knowledge.PAGES)[2],edited='2026-10-28T00:00:00Z',partial=False,text='Owner is Colby.')]
class Fake:
    def __init__(self): self.sent=[];self.calls=0
    def sources(self): return SOURCES, []
    def answer(self,q,s): self.calls+=1;return {'paragraphs':[{'text':'Owner is Colby.','sources':[1]}]}
    def reply(self,e,t): self.sent.append(t);return '20.1'

class KnowledgeTests(unittest.TestCase):
    def test_question_vs_forward(self):
        for text in ('<@BOT> Who owns the program?', '<@BOT> tell me about partnerships', '<@BOT> ask: explain referrals'):
            self.assertTrue(knowledge.question({'text':text},'BOT'))
        for msg in ({'text':'<@BOT> review this?'},{'text':'<@BOT> who is this?', 'attachments':[{}]},
                    {'text':'<@BOT> https://example.org what do you think?'},{'text':'Who owns this?'}):
            self.assertIsNone(knowledge.question(msg,'BOT'))
        self.assertTrue(knowledge.question({'text':'<@BOT> ask: explain referrals','attachments':[{}]},'BOT'))

    def test_question_and_later_eyes_share_one_job(self):
        cfg=dict(team_id='T',bot_user='BOT',knowledge_enabled=True)
        msg=dict(user=partner_sources.COLBY,ts='10.1',text='<@BOT> Who owns referrals?')
        class Forward:
            def forward_message(self,e): return msg
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/q')
            for n in range(2):
                e=dict(kind='forward_check',key='check'+str(n),event_id=str(n),channel=knowledge.CHANNEL,ts='10.1')
                store.enqueue(e);partner_sources.process_forward(store,store.get(e['key']),Forward(),cfg)
                msg['reactions']=[dict(name='eyes',users=[partner_sources.COLBY])]
            self.assertEqual(json.loads(store.get('T:'+knowledge.CHANNEL+':10.1')['payload'])['kind'],'knowledge')

    def test_eyes_first_stays_lead_and_flag_off_ignores_question(self):
        for enabled, eyes, expected in [(True,True,'review_forward'),(False,False,None)]:
            msg=dict(user=partner_sources.COLBY,text='<@BOT> Who owns this?',reactions=[dict(name='eyes',users=[partner_sources.COLBY])] if eyes else [])
            class Forward:
                def forward_message(self,e): return msg
            with tempfile.TemporaryDirectory() as d:
                store=Store(d+'/q');e=dict(key='check',event_id='e',channel=knowledge.CHANNEL,ts='10.1')
                store.enqueue(e)
                partner_sources.process_forward(store,store.get('check'),Forward(),dict(team_id='T',bot_user='BOT',knowledge_enabled=enabled))
                job=store.next_job()
                self.assertEqual(json.loads(job['payload'])['kind'] if job else None, expected)

    def test_citations_escape_and_reject_invention(self):
        text=knowledge.render({'paragraphs':[{'text':'<!channel> <@BAD>','sources':[1]}]},SOURCES,[])
        self.assertNotIn('<!channel>',text);self.assertNotIn('<@BAD>',text)
        self.assertIn('<https://app.notion.com/p/',text)
        for p in ({'text':'unsupported','sources':[]},{'text':'unsupported','sources':[2]}, {'text':'https://evil.test','sources':[1]}):
            with self.assertRaises(ValueError):knowledge.render({'paragraphs':[p]},SOURCES,[])

    def test_no_following_child_pages_synced_blocks_or_database(self):
        client=knowledge.Clients();calls=[]
        def get(path):
            calls.append(path)
            if path.startswith('pages/'):return {'last_edited_time':'2026-10-28'}
            return {'results':[{'id':'safe','type':'paragraph','paragraph':{'rich_text':[{'plain_text':'Approved text'}]}},
                {'id':'secret','type':'child_page','has_children':True},
                {'id':'external','type':'synced_block','has_children':True},
                {'id':'db','type':'child_database','has_children':True}]}
        client.get=get
        source=client.page(next(iter(knowledge.PAGES)))
        self.assertEqual(source['text'],'Approved text');self.assertTrue(source['partial']);self.assertEqual(len(calls),2)
        with self.assertRaises(ValueError):client.page('secret')

    def test_paginated_nested_and_archived(self):
        client=knowledge.Clients();calls=[]
        def get(path):
            calls.append(path)
            if path.startswith('pages/'):return {}
            if 'start_cursor=' in path:return {'results':[{'id':'p','type':'paragraph','paragraph':{'rich_text':[{'plain_text':'Last'}]}}]}
            if path.startswith('blocks/container/'):return {'results':[{'id':'p','type':'paragraph','paragraph':{'rich_text':[{'plain_text':'Nested'}]}}]}
            return {'has_more':True,'next_cursor':'next','results':[{'id':'container','type':'column','has_children':True}]}
        client.get=get;source=client.page(next(iter(knowledge.PAGES)))
        self.assertIn('Nested',source['text']);self.assertIn('Last',source['text']);self.assertEqual(len(calls),4)
        client.get=lambda p:{'archived':True}
        with self.assertRaises(ValueError):client.page(next(iter(knowledge.PAGES)))

    def test_duplicate_and_uncertain_delivery(self):
        for fail in (False,True):
            with tempfile.TemporaryDirectory() as d:
                store=Store(d+'/q');e=dict(key='k',event_id='e',kind='knowledge',question='Who?',channel='C',thread_ts='1.1')
                store.enqueue(e);client=Fake()
                if fail:
                    def reply(e,t):client.sent.append(t);raise TimeoutError()
                    client.reply=reply
                knowledge.process(store,store.get('k'),client);knowledge.process(store,store.get('k'),client)
                self.assertEqual(client.calls,1);self.assertEqual(len(client.sent),1)
                self.assertEqual(store.get('k')['reply_state'],'review' if fail else 'sent')

    def test_no_evidence_no_model(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/q');store.enqueue(dict(key='k',event_id='e',question='Who?'))
            client=Fake();client.sources=lambda:([],list(knowledge.PAGES.values()))
            knowledge.process(store,store.get('k'),client)
            self.assertEqual(client.calls,0);self.assertIn('could not read',client.sent[0])

    @patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','ANTHROPIC_MODEL':'test'})
    @patch('knowledge.urllib.request.urlopen')
    def test_model_has_no_tools_and_rejects_truncation(self,urlopen):
        urlopen.return_value.__enter__.return_value=io.BytesIO(json.dumps({'stop_reason':'max_tokens','content':[]}).encode())
        with self.assertRaises(ValueError):knowledge.Clients().answer('Who?',SOURCES)
        payload=json.loads(urlopen.call_args.args[0].data)
        self.assertNotIn('tools',payload);self.assertEqual(payload['system'],knowledge.SYSTEM)
        expected={'paragraphs':[{'text':'Owner is Colby.','sources':[1]}]}
        for text in (json.dumps(expected), '```json\n'+json.dumps(expected)+'\n```'):
            urlopen.return_value.__enter__.return_value=io.BytesIO(json.dumps({'stop_reason':'end_turn','content':[{'type':'text','text':text}]}).encode())
            self.assertEqual(knowledge.Clients().answer('Who?',SOURCES), expected)

if __name__=='__main__':unittest.main()
