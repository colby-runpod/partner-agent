import copy,json,tempfile,unittest
from unittest.mock import Mock,patch
from intake import Store
import lead_completion as c
import partner_sources as routes
from test_lead_commands import CFG

DATA=dict(company='Cormor',email='person@example.test',score='2',route='Supply Team',source_event={'key':'review','channel':'C','ts':'10.1','thread_ts':'10.1'})
ROW=dict(key='review',channel='C',ts='10.1',card_ts='11.1',outcome='ok',data=json.dumps(DATA))
class Notion:
    def __init__(self):self.props={'Status':{'select':{'name':'New'}},'Qualification Score':{'select':{'name':'2'}}};self.writes=[]
    def _req(self,method,path,payload=None):
        if method=='GET':return {'properties':self.props}
        self.writes.append(payload);self.props.update(payload['properties']);return {}

@patch.dict('os.environ',{'SLACK_BOT_TOKEN':'test'})
class Completion(unittest.TestCase):
    def body(self):return dict(team_id='T',api_app_id='A',event_id='E',event=dict(type='reaction_added',user=routes.COLBY,reaction='white_check_mark',item={'type':'message','channel':'C','ts':'11.1'}))
    def test_authority_and_not_own_bot_reactions(self):
        self.assertTrue(c.accept(self.body(),CFG))
        for k,v in [('user','BOT'),('reaction','eyes'),('type','reaction_removed')]:
            b=self.body();b['event'][k]=v;self.assertIsNone(c.accept(b,CFG))
        b=self.body();b['event']['item']['channel']='OTHER';self.assertIsNone(c.accept(b,CFG))
    def setup_flow(self,tmp):
        s=Store(tmp+'/q');e=c.accept(self.body(),CFG);s.enqueue(e);l=Mock();l.find.return_value=ROW
        return s,e,l,Notion()
    @patch('lead_completion.question_progress.change',return_value=True)
    @patch('lead_completion.lead_commands.append_activity')
    @patch('lead_completion.lead_commands.ensure_record',return_value=('https://notion.so/p','p'))
    @patch('lead_completion.api',return_value={'message':{'reactions':[{'name':'white_check_mark','users':[routes.COLBY]}]}})
    def test_handoff_status_and_audit_before_checks(self,api,ensure,append,change):
        with tempfile.TemporaryDirectory() as tmp:
            s,e,l,n=self.setup_flow(tmp);key=routes.handoff_key(DATA,DATA['source_event'],'Supply Team');s.enqueue(dict(key=key,event_id=key));s.update(key,state='done',ref='30.1',result_url='https://slack.com/handoff')
            def mark(*args):
                self.assertEqual(n.props['Status']['select']['name'],'Actioned');self.assertTrue(append.called);return True
            change.side_effect=mark;c.process(s,s.get(e['key']),Mock(),l,CFG,n)
            self.assertEqual(n.props['Intake Stage']['select']['name'],'Routed Internally');self.assertEqual(n.props['Qualification Score']['select']['name'],'2')
            self.assertEqual([call.args[0]['ts'] for call in change.call_args_list],['10.1','11.1'])
            c.process(s,s.get(e['key']),Mock(),l,CFG,n);self.assertEqual(len(n.writes),1)
    @patch('lead_completion.question_progress.change',return_value=True)
    @patch('lead_completion.lead_commands.append_activity')
    @patch('lead_completion.lead_commands.ensure_record',return_value=('https://notion.so/p','p'))
    @patch('lead_completion.api',return_value={'message':{'reactions':[{'name':'white_check_mark','users':[routes.COLBY]}]}})
    def test_preserves_closed_and_no_fake_delivery(self,api,ensure,append,change):
        with tempfile.TemporaryDirectory() as tmp:
            s,e,l,n=self.setup_flow(tmp);n.props['Status']['select']['name']='Closed';c.process(s,s.get(e['key']),Mock(),l,CFG,n)
            self.assertEqual(n.props['Status']['select']['name'],'Closed');self.assertNotIn('Intake Stage',n.props)
            self.assertIn('None recorded; no delivery inferred',append.call_args.args[-1])
    @patch('lead_completion.question_progress.change')
    @patch('lead_completion.lead_commands.ensure_record',side_effect=ValueError())
    @patch('lead_completion.api',return_value={'message':{'reactions':[{'name':'white_check_mark','users':[routes.COLBY]}]}})
    def test_notion_failure_never_marks_complete(self,api,ensure,change):
        with tempfile.TemporaryDirectory() as tmp:
            s,e,l,n=self.setup_flow(tmp);c.process(s,s.get(e['key']),Mock(),l,CFG,n);change.assert_not_called();self.assertTrue(s.get(e['key'])['error'])
    @patch('lead_completion.lead_commands.ensure_record')
    def test_question_check_is_not_lead_closure(self,ensure):
        with tempfile.TemporaryDirectory() as tmp:
            s,e,l,n=self.setup_flow(tmp);l.find.return_value=None;c.process(s,s.get(e['key']),Mock(),l,CFG,n);ensure.assert_not_called()
    def test_only_confirmed_delivery_enqueues(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp+'/q');h={'key':'handoff','event_id':'h'};s.enqueue(h)
            c.enqueue_handoff(s,h,DATA['source_event'],'Supply Team')
            with s.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM intake_jobs').fetchone()[0],1)
            s.update('handoff',state='done',ref='20.1',result_url='https://slack.com/handoff')
            c.enqueue_handoff(s,h,DATA['source_event'],'Supply Team');c.enqueue_handoff(s,h,DATA['source_event'],'Supply Team')
            with s.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM intake_jobs').fetchone()[0],2)

    @patch('lead_completion.lead_commands.ensure_record')
    @patch('lead_completion.api',return_value={'message':{'reactions':[]}})
    def test_removed_check_before_processing_is_ignored(self,api,ensure):
        with tempfile.TemporaryDirectory() as tmp:
            s,e,l,n=self.setup_flow(tmp);c.process(s,s.get(e['key']),Mock(),l,CFG,n);ensure.assert_not_called()
    @patch('lead_completion.lead_commands.ensure_record')
    def test_interrupted_notion_write_is_held_not_retried(self,ensure):
        with tempfile.TemporaryDirectory() as tmp:
            s,e,l,n=self.setup_flow(tmp);s.update(e['key'],state='writing');c.process(s,s.get(e['key']),Mock(),l,CFG,n)
            ensure.assert_not_called();self.assertEqual(s.get(e['key'])['error'],'completion_reconciliation_required')
