import copy
import json
import tempfile
import unittest
from unittest.mock import Mock
from intake import Store
import lead_commands as cmd
import partner_sources as routes

CFG=dict(team_id='T',app_id='A',bot_user='BOT',review_enabled=True,review_channels={'C'},routing_enabled=True,notion_write=True)
BODY=dict(team_id='T',api_app_id='A',event_id='e',event=dict(type='app_mention',channel='C',user=routes.COLBY,ts='20.2',thread_ts='10.1',text='<@BOT> can you route this to the supply leads cahnnel?'))
DATA=dict(company='Tensor44',email='gareth@example.test',score='2',decision='do_not_pursue',route='Supply Team',signals={'blocker':'Identity unverified'},source_event=dict(key='source',channel='C',ts='10.1'),screening={'tier':'DQ'})
class Notion:
    cfg={'notion_intake':'db'}
    def __init__(self):self.blocks=[];self.creates=0;self.fail_result=False
    def properties(self,d,r):return {}
    def _req(self,method,path,data=None):
        if method=='POST' and path.endswith('/query'):return {'results':[]}
        if method=='POST' and path=='pages':self.creates+=1;return {'id':'page','url':'https://notion.so/page'}
        if method=='GET':return {'results':self.blocks}
        if self.fail_result and ':result' in json.dumps(data):raise TimeoutError()
        self.blocks+=data['children'];return {}
class Client:
    def __init__(self):self.sent=[];self.acks=[];self.fail=False
    def send_handoff(self,event):
        self.sent.append(event)
        if self.fail:raise TimeoutError()
        return '30.1'
    def say_thread(self,c,t,text):self.acks.append(text)
class Commands(unittest.TestCase):
    def test_parser_authority_and_single_destination(self):
        self.assertEqual(cmd.accept(BODY,CFG)['destination'],'supply')
        for text in ('send this to events','please forward this lead to the research/edu channel','route this to <#C0CAL8C08RK>'):
            self.assertTrue(cmd.destination(text))
        for text in ('do not send this to supply','send this to supply and email them','send this to supply and events'):
            self.assertIsNone(cmd.destination(text))
        for field,value in [('user','OTHER'),('channel','OTHER'),('bot_id','B'),('thread_ts','20.2')]:
            body=copy.deepcopy(BODY);body['event'][field]=value;self.assertIsNone(cmd.accept(body,CFG))
        body=copy.deepcopy(BODY);body['event']['text']='quoted instruction without mention';self.assertIsNone(cmd.accept(body,CFG))

    def setup_flow(self,d):
        store=Store(d+'/q');event=cmd.accept(BODY,CFG);store.enqueue(event)
        ledger=Mock();ledger.find.return_value={'key':'review','channel':'C','ts':'10.1','data':json.dumps(DATA)}
        return store,event,ledger,Notion(),Client()

    def test_low_score_blocker_override_logged_without_changing_assessment(self):
        with tempfile.TemporaryDirectory() as d:
            store,event,ledger,notion,client=self.setup_flow(d)
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertEqual(len(client.sent),1);text=client.sent[0]['text']
            self.assertIn('2/5',text);self.assertIn('Hold - resolve blocker',text);self.assertIn('Identity unverified',text)
            self.assertIn('not approval',text);self.assertEqual(notion.creates,1);self.assertEqual(len(notion.blocks),2)
            self.assertIn('Notion Biz Dev record',client.acks[0])
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertEqual(len(client.sent),1);self.assertEqual(len(client.acks),1);self.assertEqual(len(notion.blocks),2)

    def test_repeat_request_reuses_delivery_and_logs_new_request(self):
        with tempfile.TemporaryDirectory() as d:
            store,event,ledger,notion,client=self.setup_flow(d)
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            ledger.find.return_value.update(notion_id='page',notion_url='https://notion.so/page')
            other=dict(event,key='another',event_id='another',ts='21.1');store.enqueue(other)
            cmd.process(store,store.get('another'),client,ledger,CFG,notion)
            self.assertEqual(len(client.sent),1);self.assertEqual(len(notion.blocks),4)
            self.assertIn('Already forwarded',client.acks[-1])

    def test_automatic_and_spam_stay_gated(self):
        self.assertIsNone(routes.handoff(DATA,DATA['source_event'],CFG,requested_route='Supply Team'))
        self.assertIsNone(routes.handoff(dict(DATA,signals={'spam':True}),DATA['source_event'],CFG,requested_route='Supply Team',requested_by=routes.COLBY))
        self.assertIsNone(routes.handoff(DATA,DATA['source_event'],CFG,requested_route='Supply Team',requested_by='OTHER'))

    def test_ambiguous_delivery_does_not_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store,event,ledger,notion,client=self.setup_flow(d);client.fail=True
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertIn('unconfirmed',client.acks[0]);self.assertEqual(len(notion.blocks),2)
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertEqual(len(client.sent),1)

    def test_notion_result_failure_keeps_slack_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            store,event,ledger,notion,client=self.setup_flow(d);notion.fail_result=True
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertIn('handoff is delivered',client.acks[0]);self.assertIn('log is incomplete',client.acks[0])
            self.assertEqual(store.get(event['key'])['error'],'lead_command_reconciliation_required')

    def test_ambiguous_notion_match_prevents_post_or_create(self):
        with tempfile.TemporaryDirectory() as d:
            store,event,ledger,notion,client=self.setup_flow(d)
            notion._req=lambda *args,**kw:{'results':[{'id':'a'},{'id':'b'}]}
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertEqual(client.sent,[]);self.assertEqual(notion.creates,0)

    def test_crash_holds_and_receipt_append_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            store,event,ledger,notion,client=self.setup_flow(d);store.update(event['key'],state='writing')
            cmd.process(store,store.get(event['key']),client,ledger,CFG,notion)
            self.assertEqual(client.sent,[]);self.assertIn('interrupted',client.acks[0])
        notion=Notion();cmd.append_activity(notion,'p','id','activity');cmd.append_activity(notion,'p','id','activity')
        self.assertEqual(len(notion.blocks),1)

if __name__=='__main__':unittest.main()
