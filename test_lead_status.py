import json,unittest
from unittest.mock import patch
import lead_status as s
class Status(unittest.TestCase):
 def row(self):return dict(key='k',channel=s.CHANNEL,ts='1.1',card_ts='2.2',data='{}',parked_by=None)
 @patch('lead_status.question_progress.change',return_value=True)
 @patch('lead_status.message',return_value={'reactions':[{'name':'white_check_mark','users':['human']}]})
 @patch.dict('os.environ',SLACK_BOT_TOKEN='test')
 def test_clock_does_not_remove_human_check(self,api,change):
  self.assertTrue(s.sync_row(self.row(),[]))
  self.assertEqual([c.args[1:] for c in change.call_args_list],[('add','clock3'),('add','clock3')])
 @patch('lead_status.question_progress.change',return_value=True)
 @patch('lead_status.message',return_value={'reactions':[{'name':'clock3','users':[s.BOT]}]})
 @patch.dict('os.environ',SLACK_BOT_TOKEN='test')
 def test_confirmed_completion_replaces_clock(self,api,change):
  receipt=dict(payload=json.dumps(dict(channel=s.CHANNEL,review_key='k')),ref=json.dumps(dict(notion_id='p',status='Actioned')),result_url='https://notion.so/p')
  s.sync_row(self.row(),[receipt])
  self.assertEqual([c.args[1:] for c in change.call_args_list],[('add','white_check_mark'),('remove','clock3')]*2)
 @patch('lead_status.question_progress.change',return_value=False)
 @patch('lead_status.message',return_value={'reactions':[{'name':'white_check_mark','users':[s.BOT]}]})
 @patch.dict('os.environ',SLACK_BOT_TOKEN='test')
 def test_failed_add_retains_existing_marker(self,api,change):
  self.assertFalse(s.sync_row(self.row(),[]))
  self.assertTrue(all(c.args[1]=='add' for c in change.call_args_list))
 @patch('lead_status.question_progress.change',return_value=True)
 @patch('lead_status.message',return_value={'reactions':[]})
 @patch.dict('os.environ',SLACK_BOT_TOKEN='test')
 def test_park_is_not_completion(self,api,change):
  r=self.row();r['parked_by']='colby';s.sync_row(r,[])
  self.assertTrue(all(c.args[2]=='double_vertical_bar' for c in change.call_args_list))
