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
            self.assertTrue(api.call_args.args[2]['text'].endswith('cc <@'+src.COLBY+'>'))
        client.post(dict(kind='review_email',source_url='https://mail.google.com/mail/u/0/#all/abc'),'result')
        self.assertEqual(api.call_args.args[2]['channel'],'COUT')
        self.assertNotIn('thread_ts',api.call_args.args[2])

class CardLayout(unittest.TestCase):
    def test_score_and_assessment_follow_region(self):
        for route in ('Supply Team', 'Events/Sponsorship'):
            r=dict(route=route,screening={'tier':'Qualify'},score=2,decision='triage',region='US',signals={})
            text=src.handoff(r,dict(key='k',channel='C123',ts='100.1'),CFG)['text']
            self.assertIn('• *Region:* US\n• *Lead score:* 2️⃣ 2/5\n• *Assessment:* 🟡 Needs triage',text)
            self.assertIn('*Suggested review:* Confirm',text)
            self.assertTrue(text.endswith('cc <@'+src.COLBY+'>'))
        self.assertEqual(src.score_badge({'score':'DQ'}),'DQ')
        self.assertEqual(src.score_badge({}),'Not scored')

class RoutingActions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name+'/db'); self.ledger=review.Ledger(self.store.path)
        self.cfg=dict(CFG,interactive=True,feedback_users={src.COLBY},shadow_channel='COUT')
        self.event=dict(key='T1:C123:100.1',event_id='e',kind='review',channel='C123',ts='100.1',thread_ts='100.1',text='Lead')
        self.data=dict(company='Test',contact='Person',email='person@example.com',score='2',decision='do_not_pursue',
                       route='Supply Team',screening={'tier':'Park'},signals={},region='US',summary='Small supplier')
        self.save()
        self.client=Mock(); self.client.send_handoff.return_value='200.1'

    def save(self):
        self.ledger.record(self.event['key'],self.event,self.data,'ok')
        self.ledger.mark(self.event['key'],card_text='review')
        self.ledger.delivered(self.event['key'],'sent',card_ts='100.2')

    def act(self,action='route_supply',user=src.COLBY,ts='300.1'):
        event=dict(kind='action',key='act:'+ts,event_id='act:'+ts,review_key=self.event['key'],
                   action=action,user=user,channel='C123',message_ts='100.2')
        self.store.enqueue(event)
        review.process_action(self.store,self.store.get(event['key']),self.client,self.ledger,self.cfg)

    def test_manual_low_score_routes_once_without_notion(self):
        self.assertIsNone(src.handoff(self.data,self.event,self.cfg))
        self.act(); self.act(ts='300.2')
        self.client.send_handoff.assert_called_once()
        sent=self.client.send_handoff.call_args.args[0]
        self.assertEqual(sent['channel'],'C0CAL8C08RK')
        self.assertIn('2️⃣ 2/5',sent['text'])
        self.assertTrue(sent['text'].endswith('cc <@'+src.COLBY+'>'))
        self.assertIn('Open handoff',self.ledger.review(self.event['key'])['notes'])

    def test_event_destination_and_mentions(self):
        self.act('route_events')
        sent=self.client.send_handoff.call_args.args[0]
        self.assertEqual(sent['channel'],'C0CAKA6PW8K')
        self.assertIn('<@U0AB1TSBK5X>',sent['text']); self.assertIn('<@U0APRAUGJ6P>',sent['text'])
        self.assertIn('Confirm the sponsorship ask',sent['text'])

    def test_uncertain_send_is_never_retried_or_reported_delivered(self):
        self.client.send_handoff.side_effect=TimeoutError()
        self.act(); self.act(ts='300.2')
        self.client.send_handoff.assert_called_once()
        notes=self.ledger.review(self.event['key'])['notes']
        self.assertIn('Delivery unconfirmed',notes); self.assertNotIn('Delivered to',notes)

    def test_automatic_and_manual_share_receipt(self):
        job=src.handoff(dict(self.data,screening={'tier':'Qualify'}),self.event,self.cfg)
        self.store.enqueue(job); src.process_handoff(self.store,self.store.get(job['key']),self.client)
        self.act(); self.client.send_handoff.assert_called_once()

    def test_only_colby_and_no_spam_blocked_or_in_play(self):
        self.act(user='UOTHER'); self.client.send_handoff.assert_not_called()
        for i,signal in enumerate(('spam','blocker','already_in_play')):
            self.data['signals']={signal:True}; self.save(); self.act(ts='400.'+str(i))
        self.client.send_handoff.assert_not_called()

    def test_old_card_missing_signals_cannot_route(self):
        self.data.pop('signals'); self.save(); self.act()
        self.client.send_handoff.assert_not_called()
        self.assertIn('older card',self.client.ephemeral.call_args.args[1])

    def test_review_layout(self):
        from test_review import GOOD
        card=review.render(review.parse(json.dumps(GOOD)))
        self.assertLess(card.index('*Suggested next step:*'),card.index('*Checks*'))
        for label in ('Identity','Company','Fit','Potential'):
            self.assertIn('• *'+label+':*',card)

    def test_button_visibility_and_actor_validation(self):
        from test_review import click
        data=dict(self.data,key_for_buttons=self.event['key'])
        ids=[a for a,k in review.action_buttons(self.cfg,data)]
        self.assertIn('route_supply',ids); self.assertIn('route_events',ids)
        blocked=dict(data,signals={'spam':True})
        self.assertNotIn('route_supply',[a for a,k in review.action_buttons(self.cfg,blocked)])
        cfg=dict(self.cfg,feedback_users={src.COLBY,'UOTHER'})
        self.assertIsNone(review.accept_action(click('route_supply',user='UOTHER'),cfg))
        self.assertIsNotNone(review.accept_action(click('route_supply',user=src.COLBY),cfg))

    def test_route_buttons_require_matching_card(self):
        self.ledger.delivered(self.event['key'],'sent',card_ts='999.1')
        self.act(); self.client.send_handoff.assert_not_called()
        self.assertIn('does not match',self.client.ephemeral.call_args.args[1])
