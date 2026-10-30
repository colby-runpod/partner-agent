import copy,json,tempfile,unittest
from unittest.mock import Mock,patch
from intake import Store
import question_progress as p
import lead_qa
from test_lead_commands import CFG,BODY

class Progress(unittest.TestCase):
    def event(self):
        b=copy.deepcopy(BODY);b['event']['text']='<@BOT> Is this sales?';return lead_qa.accept(b,CFG)
    @patch('question_progress.change',return_value=True)
    def test_receipt_then_work_then_completed(self,change):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d+'/q');e=self.event();s.enqueue(e)
            p.received(s,e);self.assertEqual(change.call_args.args[2],'eyes')
            s.update(e['key'],state='processing');p.working(e)
            self.assertIn((e,'add','hourglass_flowing_sand'),[c.args for c in change.call_args_list])
            s.update(e['key'],state='done',reply_state='sent');p.finished(s,e['key'],e)
            self.assertIn((e,'add','white_check_mark'),[c.args for c in change.call_args_list])
            change.reset_mock();p.received(s,e);change.assert_not_called()
    @patch('question_progress.change',return_value=True)
    def test_failures_and_uncertain_delivery_never_green(self,change):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d+'/q');e=self.event();s.enqueue(e)
            for state,error in [('sent','lead_qa_unavailable'),('review','reply_unconfirmed')]:
                s.update(e['key'],state='done',reply_state=state,error=error);p.finished(s,e['key'],e)
            adds=[c.args[2] for c in change.call_args_list if c.args[1]=='add']
            self.assertEqual(adds,['warning','warning'])
    @patch('question_progress.change',return_value=True)
    def test_ack_race_removes_late_received_without_regressing(self,change):
        s=Mock();s.get.side_effect=[{'state':'pending'},{'state':'done'}];e=self.event()
        p.received(s,e)
        self.assertEqual([(c.args[1],c.args[2]) for c in change.call_args_list],[('add','eyes'),('remove','eyes')])
    @patch('question_progress.change',return_value=False)
    def test_failed_add_does_not_remove_previous_status(self,change):
        p.working(self.event());self.assertEqual(change.call_count,1)
    @patch('question_progress.change',return_value=True)
    def test_unmarked_automatic_event_is_untouched(self,change):
        e=self.event();e.pop('progress');p.received(Mock(),e);p.working(e);p.finished(Mock(),'k',e);change.assert_not_called()
    @patch('lead_qa.context',return_value=[])
    @patch('lead_qa.answer',return_value='Answer')
    @patch('question_progress.change',return_value=True)
    def test_check_only_after_confirmed_answer(self,change,answer,context):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d+'/q');e=self.event();s.enqueue(e);client=Mock()
            def reply(*args):
                self.assertNotIn('white_check_mark',[c.args[2] for c in change.call_args_list if c.args[1]=='add'])
            client.say_thread.side_effect=reply
            lead_qa.process(s,s.get(e['key']),client,Mock(),CFG)
            self.assertEqual([c.args[2] for c in change.call_args_list if c.args[1]=='add'],['hourglass_flowing_sand','white_check_mark'])
