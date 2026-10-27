import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import Mock
import review
from test_review import CFG, GOOD, FakeSay


class DailySummaryDelivery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.ledger = review.Ledger(tmp.name+'/db')
        self.now = dt.datetime(2026,10,27,23,0)
        self.client = FakeSay()

    def test_receipt_saved_and_window_advanced_only_on_success(self):
        review.run_summary(CFG,self.ledger,self.client,self.now)
        state=review.summary_state(self.ledger)
        self.assertIsNone(state['pending']); self.assertEqual(state['receipt']['ts'],'500.1')
        self.assertEqual(state['through'],self.now.replace(tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(state['receipt']['start'],dt.datetime(2026,10,27,6,tzinfo=dt.timezone.utc).timestamp())
        self.assertFalse(review.run_summary(CFG,self.ledger,self.client,self.now))

    def test_uncertain_send_keeps_window_and_blocks_next_day(self):
        self.client.say=Mock(side_effect=TimeoutError())
        review.run_summary(CFG,self.ledger,self.client,self.now)
        state=review.summary_state(self.ledger)
        self.assertNotIn('through',state); self.assertEqual(state['pending']['status'],'unconfirmed')
        self.assertFalse(review.run_summary(CFG,self.ledger,self.client,self.now+dt.timedelta(days=1)))
        self.client.say.assert_called_once()

    def test_missing_receipt_is_unconfirmed(self):
        self.client.say=Mock(return_value=None)
        review.run_summary(CFG,self.ledger,self.client,self.now)
        self.assertEqual(review.summary_state(self.ledger)['pending']['status'],'unconfirmed')

    def test_crash_after_send_does_not_resend(self):
        put=self.ledger.put
        def fail_final(key,value):
            if json.loads(value).get('receipt'): raise RuntimeError('commit unavailable')
            put(key,value)
        self.ledger.put=fail_final
        with self.assertRaises(RuntimeError): review.run_summary(CFG,self.ledger,self.client,self.now)
        self.ledger.put=put
        self.assertEqual(review.summary_state(self.ledger)['pending']['status'],'sending')
        self.assertFalse(review.run_summary(CFG,self.ledger,self.client,self.now))
        self.assertEqual(len(self.client.said),1)

    def test_weekends_and_late_arrivals_carried_into_monday(self):
        friday=dt.datetime(2026,10,30,23,0)
        review.run_summary(CFG,self.ledger,self.client,friday)
        start=review.summary_state(self.ledger)['through']
        self.assertFalse(review.run_summary(CFG,self.ledger,self.client,friday+dt.timedelta(days=1)))
        review.run_summary(CFG,self.ledger,self.client,dt.datetime(2026,11,3,0,0)) # Monday 5 MST
        self.assertEqual(review.summary_state(self.ledger)['receipt']['start'],start)

    def test_dedupes_rereviews_and_excludes_gmail_and_synthetic_tests(self):
        for key,created,kind,synthetic in [('first',1,'review',False),('new',2,'review',False),
                                           ('email',3,'review_email',False),('test',4,'review',True)]:
            data=review.parse(json.dumps(GOOD))
            event=dict(channel='C1',ts='100.1',thread_ts='100.1',kind=kind,synthetic_test=synthetic)
            self.ledger.record(key,event,data,'ok',now=created)
        text=review.build_summary(CFG,self.ledger.since(0),self.client,self.now)
        self.assertIn('1 reviewed:',text); self.assertIn('Claude/Gmail leads excluded',text)
        self.assertEqual(text.count('|Example Inc>'),1)

    def test_handoff_status_uses_receipt_not_assumed_route(self):
        data=review.parse(json.dumps(GOOD));data['email']='a@example.com'
        self.ledger.record('k',dict(channel='C1',ts='100.1',thread_ts='100.1'),data,'ok',now=1)
        store=Mock();store.get.side_effect=[{'state':'done','ref':'1.2','result_url':'https://slack.com/archives/C1/p12'},
                                           {'state':'done','ref':None,'result_url':None}]
        text=review.build_summary(CFG,self.ledger.since(0),self.client,self.now,store)
        self.assertIn('Delivered to Dean',text);self.assertIn('Adriana and Carmela: unconfirmed',text)
