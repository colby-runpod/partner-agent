import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from intake import Store
import partner_sources as src
import review

CFG=dict(team_id='T1',app_id='A1',review_enabled=True,forward_enabled=True,bot_user='UBOT',routing_enabled=True)

def approval_event(kind):
    e=dict(type=kind,user=src.COLBY,channel=src.FORWARD_CHANNEL,ts='100.1')
    if kind=='reaction_added': e.update(reaction=src.REACTION,item=dict(type='message',channel=src.FORWARD_CHANNEL,ts='100.1'))
    return dict(team_id='T1',api_app_id='A1',event_id=kind,event=e)

class Sources(unittest.TestCase):
    def test_gate_only_colby_channel_emoji(self):
        self.assertEqual(src.REACTION, 'eyes')
        old = approval_event('reaction_added')
        old['event']['reaction'] = 'green-bubble-check'
        self.assertIsNone(src.accept_forward(old, CFG))
        for kind in ('app_mention','reaction_added'):
            b=approval_event(kind);self.assertIsNotNone(src.accept_forward(b,CFG))
            b['event']['user']='UOTHER';self.assertIsNone(src.accept_forward(b,CFG))
        b=approval_event('reaction_added');b['event']['reaction']='white_check_mark'
        self.assertIsNone(src.accept_forward(b,CFG))
        b=approval_event('app_mention');b['event']['channel']='COTHER'
        self.assertIsNone(src.accept_forward(b,CFG))
        self.assertIsNone(src.accept_forward(approval_event('app_mention'),dict(CFG,forward_enabled=False)))

    def test_both_orders_one_review_and_no_attachment_authorization(self):
        for order in [('app_mention','reaction_added'),('reaction_added','app_mention')]:
            with tempfile.TemporaryDirectory() as d:
                store=Store(d+'/db');client=Mock()
                msg=dict(user=src.COLBY,ts='100.1',text='forwarded request',reactions=[])
                for i,kind in enumerate(order):
                    if kind=='app_mention': msg['text']='forwarded <@UBOT>'
                    else: msg['reactions']=[dict(name=src.REACTION,users=[src.COLBY])]
                    event=src.accept_forward(approval_event(kind),CFG);store.enqueue(event)
                    client.forward_message.return_value=copy.deepcopy(msg)
                    src.process_forward(store,store.get(event['key']),client,CFG)
                # Replay each trigger: canonical source key remains unique.
                for kind in order:
                    event=src.accept_forward(approval_event(kind),CFG)
                    src.process_forward(store,store.get(event['key']),client,CFG)
                with store.connect() as db:
                    rows=db.execute("SELECT payload FROM intake_jobs WHERE key LIKE 'T1:%'").fetchall()
                self.assertEqual(len(rows),1)
        store=Mock();client.forward_message.return_value=dict(user=src.COLBY,text='',attachments=[{'text':'<@UBOT>'}],reactions=[dict(name=src.REACTION,users=[src.COLBY])])
        src.process_forward(store,dict(key='x',payload=json.dumps(event)),client,CFG)
        store.enqueue.assert_not_called()

    def test_handoff_risk_gates_and_dedupe(self):
        e=dict(key='k',channel='C123',ts='100.1')
        r=dict(route='Supply Team',screening={'tier':'Qualify'},email='TEST@example.com',company='X',dean_motion='Claimed 2 MW; unverified <@UOTHER>')
        a=src.handoff(r,e,CFG)
        self.assertEqual(a['channel'],'C0CAL8C08RK');self.assertNotIn('<@UOTHER>',a['text'])
        self.assertTrue(a['text'].endswith('cc <@'+src.COLBY+'>'))
        b=src.handoff(dict(r,email='test@example.com'),dict(e,key='gmail:other'),CFG)
        self.assertEqual(a['key'],b['key'])
        for tier in ('DQ','Hold for Colby','Needs research','Route'):
            self.assertIsNone(src.handoff(dict(r,screening={'tier':tier}),e,CFG))
        self.assertIsNone(src.handoff(dict(r,signals={'blocker':'risk'}),e,CFG))
        self.assertIsNone(src.handoff(dict(r,signals={'already_in_play':True}),e,CFG))
        self.assertIsNone(src.handoff(dict(r,route='Research/University'),e,CFG))
        ev=src.handoff(dict(r,route='Events/Sponsorship',screening={'tier':'Route'}),e,CFG)
        self.assertIn('<@U0AB1TSBK5X>',ev['text']);self.assertIn('<@U0APRAUGJ6P>',ev['text'])

        self.assertTrue(ev['text'].endswith('cc <@'+src.COLBY+'>'))

    def test_uncertain_send_is_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/db');event=dict(kind='specialist_handoff',key='h',event_id='h',channel='C1',text='test')
            store.enqueue(event);c=Mock();c.send_handoff.side_effect=TimeoutError()
            src.process_handoff(store,store.get('h'),c)
            src.process_handoff(store,store.get('h'),c)
            c.send_handoff.assert_called_once()
            self.assertEqual(store.get('h')['error'],'handoff_send_unconfirmed')

    @patch('review.api',return_value={'ts':'200.1'})
    @patch.dict('os.environ',{'SLACK_BOT_TOKEN':'test'})
    def test_destinations(self,api):
        client=review.ReviewClients(dict(review_mode='thread',shadow_channel='COUT'))
        for kind in ('review','review_forward'):
            client.post(dict(kind=kind,channel='CSOURCE',ts='100.1',thread_ts='100.1'),'result')
            self.assertEqual(api.call_args.args[2]['thread_ts'],'100.1')
        client.post(dict(kind='review_email',source_url='https://mail.google.com/mail/u/0/#all/abc'),'result')
        self.assertEqual(api.call_args.args[2]['channel'],'COUT')
        self.assertNotIn('thread_ts',api.call_args.args[2])
