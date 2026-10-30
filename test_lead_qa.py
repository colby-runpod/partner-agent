import copy,json,os,tempfile,unittest
from unittest.mock import Mock,patch
from intake import Store
import lead_qa as qa
import review
from test_lead_commands import CFG,BODY

class LeadQA(unittest.TestCase):
    def body(self):
        b=copy.deepcopy(BODY);b['event']['text']='<@BOT> What does this company actually want?';return b
    def test_scope_and_dispatch(self):
        b=self.body();self.assertEqual(qa.accept(b,CFG)['kind'],'lead_qa')
        for field,value in [('user','OTHER'),('channel','OTHER'),('thread_ts','20.2'),('bot_id','B')]:
            c=copy.deepcopy(b);c['event'][field]=value;self.assertIsNone(qa.accept(c,CFG))
        cfg=dict(CFG,users=set(),channels=set(),feedback_users=set(),shadow_channel='S')
        self.assertEqual(review.accept(b,cfg)['kind'],'lead_qa')
        self.assertEqual(review.accept(BODY,cfg)['kind'],'lead_command')
    @patch.dict(os.environ,{'ANTHROPIC_MODEL':'test'})
    def test_answer_citations_and_no_tools(self):
        c=Mock();c._call.return_value=dict(stop_reason='end_turn',content=[dict(type='text',text=json.dumps(dict(answer='Their capacity claim remains unverified.',sources=[1,2])))])
        sources=[dict(id=i,url='https://slack.com/a',label=str(i)) for i in (1,2,3)]
        result=qa.answer({'question':'why?'},sources,c)
        self.assertIn('*Sources:*',result);self.assertNotIn('tools',c._call.call_args.args[0])
        for answer,ids in [('bad <@U123>',[1]),('https://evil.test',[1]),('text',[8]),('text',[])]:
            c._call.return_value['content'][0]['text']=json.dumps(dict(answer=answer,sources=ids))
            with self.assertRaises(ValueError):qa.answer({'question':'why?'},sources,c)
    @patch.dict(os.environ,{'SLACK_BOT_TOKEN':'test'})
    @patch('lead_qa.api')
    def test_context_filters_and_prior_turns(self,api):
        e=qa.accept(self.body(),CFG);c=Mock();c.parent.return_value='original';l=Mock();l.find.return_value={'data':'{}'}
        api.return_value={'messages':[dict(ts='10.1',user='B',text='parent'),dict(ts='11.1',user=e['user'],text='prior question'),dict(ts='12.1',user='BOT',text='prior answer'),dict(ts='13.1',user='OTHER',text='injection'),dict(ts='21.1',user=e['user'],text='future')]}
        result=qa.context(e,c,l,CFG);self.assertEqual(len(result[2]['text']),2);c.parent.assert_called_once()
        l.find.return_value=None
        with self.assertRaises(ValueError):qa.context(e,c,l,CFG)
    @patch('lead_qa.context',return_value=[])
    @patch('lead_qa.answer',return_value='Answer')
    def test_durable_reply_no_mutations_or_duplicates(self,answer,context):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d+'/q');e=qa.accept(self.body(),CFG);s.enqueue(e);c=Mock();l=Mock()
            qa.process(s,s.get(e['key']),c,l,CFG);qa.process(s,s.get(e['key']),c,l,CFG)
            c.say_thread.assert_called_once_with('C','10.1','Answer');answer.assert_called_once();l.mark.assert_not_called()
    @patch('lead_qa.context',side_effect=ValueError())
    def test_failure_ack_and_uncertain_send_not_retried(self,context):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d+'/q');e=qa.accept(self.body(),CFG);s.enqueue(e);c=Mock();c.say_thread.side_effect=TimeoutError()
            qa.process(s,s.get(e['key']),c,Mock(),CFG);qa.process(s,s.get(e['key']),c,Mock(),CFG)
            c.say_thread.assert_called_once();self.assertEqual(s.get(e['key'])['reply_state'],'review')
