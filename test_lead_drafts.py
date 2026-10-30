import copy,json,os,tempfile,unittest
from unittest.mock import Mock,patch
from intake import Store
import lead_drafts as d
import review
from test_lead_commands import CFG,BODY

class Drafts(unittest.TestCase):
    def event(self,text='okay can we draft an email to this person.',ts='20.2'):
        b=copy.deepcopy(BODY);b['event_id']='event:'+ts;b['event'].update(text='<@BOT> '+text,ts=ts);return d.accept(b,CFG)
    def test_intent_authority_and_dispatch(self):
        self.assertEqual(self.event()['action'],'draft')
        for text,action in [('make it shorter','revise'),('save this draft to Gmail','gmail'),('send this email','send')]:
            self.assertEqual(self.event(text)['action'],action)
        self.assertIsNone(self.event('is this sales?'));self.assertIsNone(self.event('route this to supply'))
        b=copy.deepcopy(BODY);b['event']['text']='<@BOT> draft an email';b['event']['user']='OTHER';self.assertIsNone(d.accept(b,CFG))
    @patch.dict(os.environ,{'ANTHROPIC_MODEL':'test'})
    def test_generate_uses_fixed_recipient_and_safe_rendering(self):
        c=Mock();c._call.return_value=dict(stop_reason='end_turn',content=[dict(type='text',text=json.dumps(dict(subject='RunPod reply',body='Hello <@U123> — thanks.\n\nThanks,\nColby',to='attacker@example.test')))])
        draft=d.generate(self.event(),[{},dict(text={'email':'real@example.test'})],None,c)
        self.assertEqual(draft['to'],'real@example.test');self.assertEqual(draft['subject'],'Runpod reply')
        self.assertNotIn('Colby',draft['body']);self.assertNotIn('<@U123>',d.render(draft));self.assertIn('not sent',d.render(draft));self.assertNotIn('tools',c._call.call_args.args[0])
        with self.assertRaises(ValueError):d.generate(self.event(),[{},dict(text={'email':''})],None,c)
    @patch('lead_drafts.generate',return_value={'to':'real@example.test','subject':'Hello','body':'Draft body'})
    @patch('lead_drafts.lead_qa.context',return_value=[])
    def test_revision_context_dedupe_and_no_notion_write(self,context,generate):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp+'/q');e=self.event();s.enqueue(e);c=Mock();l=Mock();n=Mock()
            d.process(s,s.get(e['key']),c,l,CFG,n);d.process(s,s.get(e['key']),c,l,CFG,n)
            c.say_thread.assert_called_once();self.assertFalse(n.method_calls)
            e2=self.event('make it shorter','21.2');s.enqueue(e2);d.process(s,s.get(e2['key']),c,l,CFG,n)
            self.assertEqual(generate.call_args.args[2]['body'],'Draft body')
            other=dict(e2,thread_ts='99.1');self.assertIsNone(d.latest(s,other))
            self.assertIsNone(d.latest(s,dict(e2,ts='19.1')))
    def test_missing_prior_draft_and_send_never_call_model_or_gmail(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp+'/q');c=Mock();n=Mock()
            for text in ('make it shorter','save this draft to Gmail','send this email'):
                e=self.event(text,str(20+len(c.say_thread.call_args_list))+'.2');s.enqueue(e);d.process(s,s.get(e['key']),c,Mock(),CFG,n)
            c._call.assert_not_called();self.assertFalse(n.method_calls)
    @patch('lead_drafts.lead_commands.ensure_record',return_value=('https://notion.so/page','page'))
    def test_gmail_request_exact_draft_pending_only_and_no_duplicates(self,ensure):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp+'/q');prior=self.event();s.enqueue(prior);draft=dict(to='real@example.test',subject='Custom subject',body='Reviewed body')
            s.update(prior['key'],state='done',reply_state='sent',ref=json.dumps({'draft':draft}))
            e=self.event('save this draft to Gmail','21.2');s.enqueue(e);c=Mock();l=Mock();l.find.return_value={'data':json.dumps({'email':draft['to']})};n=Mock()
            d.process(s,s.get(e['key']),c,l,CFG,n);d.process(s,s.get(e['key']),c,l,CFG,n)
            n.request_draft.assert_called_once_with('page',{'email':draft['to'],'draft_subject':'Custom subject'},'Reviewed body')
            self.assertIn('not confirmed in Gmail',c.say_thread.call_args.args[2]);c.say_thread.assert_called_once()
    def test_pending_notion_request_blocks_duplicate_and_preserves_sender(self):
        n=review.NotionWriter({'notion_intake':'db'});n._req=Mock(return_value={})
        n.request_draft('p',{'email':'a@example.test','draft_subject':'Custom'},'Body')
        note=n._req.call_args.args[2]['properties']['Next Step Notes']['rich_text'][0]['text']['content']
        self.assertIn('partners@runpod.io',note);self.assertIn('signature named partners@',note);self.assertIn('never send',note);self.assertIn('Custom',note)
        n._req=Mock(return_value={'properties':{'Next Step Notes':{'rich_text':[{'plain_text':note}]}}})
        with self.assertRaises(review.AlreadyDrafted):n.request_draft('p',{'email':'a@example.test'},'Body')
        n._req.assert_called_once()
    def test_interrupted_handoff_does_not_repeat_external_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp+'/q');e=self.event('save this draft to Gmail');s.enqueue(e);s.update(e['key'],state='writing');n=Mock();c=Mock()
            d.process(s,s.get(e['key']),c,Mock(),CFG,n)
            self.assertFalse(n.method_calls);self.assertIn('reconciliation',c.say_thread.call_args.args[2])

    @patch.dict(os.environ,{'ANTHROPIC_MODEL':'test'})
    def test_prospect_availability_is_not_runpod_availability(self):
        c=Mock();c._call.return_value=dict(stop_reason='end_turn',content=[dict(type='text',text=json.dumps(dict(subject='Hello',body='Weekdays or weekends both work for us.')))])
        with self.assertRaises(ValueError):d.generate(self.event(),[{},dict(text={'email':'real@example.test'})],None,c)

    def test_slack_draft_preserves_apostrophes_and_escapes_mentions(self):
        text=d.render(dict(to='a@example.test',subject="Let's talk",body="You're welcome. <@U123>"))
        self.assertIn("Let's talk",text);self.assertIn("You're welcome",text)
        self.assertNotIn('&#x27;',text);self.assertNotIn('<@U123>',text)
