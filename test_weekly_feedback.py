import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import outbox as o
import weekly_feedback as w
from test_outbox import Remote, page
from test_weekly_outbox import weekly

DRAFT = '00000000-0000-0000-0000-000000000999'

class FeedbackRemote(Remote):
    def __init__(self, pages):
        super().__init__(pages)
        self.pages[DRAFT] = {'id': DRAFT, 'parent': {'data_source_id': w.WEEKLY_SOURCE},
                            'properties': {'Week': {'type': 'date', 'date': {'start': '2026-11-02'}},
                                           'Message Content': dict(type='rich_text', **o.rich('Original draft'))}}
        self.blocks = {}; self.append_count = {}; self.fail_destination = None; self.accept_timeout = False
        self.duplicate = False; self.query_count = 0; self.has_more = False
    def notion(self, method, path, data=None):
        if path.endswith('/query'):
            self.query_count += 1
            return {'results': [copy.deepcopy(self.pages[DRAFT])] * (2 if self.duplicate else 1), 'has_more': self.has_more}
        id = path.split('/')[1]
        if method == 'GET':
            return {'results': copy.deepcopy(self.blocks.get(id, [])), 'has_more': False}
        self.append_count[id] = self.append_count.get(id, 0) + 1
        if id == self.fail_destination and not self.accept_timeout:
            raise TimeoutError()
        self.blocks.setdefault(id, []).extend(copy.deepcopy(data['children']))
        if id == self.fail_destination:
            raise TimeoutError()
        return {'results': data['children']}

class WeeklyFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ, WEEKLY_FEEDBACK_ENABLED='true', WEEKLY_OUTBOX_ENABLED='true', SLACK_TEAM_ID='T1')
        env.start(); self.addCleanup(env.stop)
        self.p=weekly(); self.remote=FeedbackRemote([self.p])
        self.box=o.Outbox(self.tmp.name+'/db',self.remote)
        self.box.post(self.p)
        self.parent=self.box.get(self.p['id'])['ts']
    def event(self, **changes):
        e=dict(type='app_mention',channel=o.WEEKLY_CHANNEL,user=o.COLBY,ts='1793133459.4',thread_ts=self.parent,
               text='<@'+o.BOT+'> Please add the marketplace launch update.')
        e.update(changes)
        return dict(team_id='T1',api_app_id='A1',event_id='E1',event=e)
    def enqueue(self, **changes):
        event=w.accept(self.event(**changes));self.box.enqueue_event(event);return event
    def pending(self):
        return [json.loads(e['payload']) for e in self.box.pending_events()]
    def test_authorized_mentions_only_in_owned_threads(self):
        cfg=dict(outbox_enabled=True,team_id='T1',app_id='A1')
        with patch.object(o,'owns',return_value=True):
            self.assertEqual(o.accept(self.event(),cfg)['kind'],'weekly_feedback')
            self.assertIsNotNone(o.accept(self.event(user='U0C6DH3UUV9'),cfg))
            for fields in ({'user':'UOTHER'},{'type':'message'},{'text':'plain feedback'}, {'thread_ts':''},
                           {'thread_ts':'1793133459.4'}, {'channel':'OTHER'}, {'bot_id':'B1'}, {'subtype':'message_changed'}):
                self.assertIsNone(o.accept(self.event(**fields),cfg))
            b=self.event();b['team_id']='OTHER';self.assertIsNone(o.accept(b,cfg))
            b=self.event();b['api_app_id']='OTHER';self.assertIsNone(o.accept(b,cfg))
        with patch.object(o,'owns',return_value=False):self.assertIsNone(o.accept(self.event(),cfg))
        with patch.dict(os.environ,WEEKLY_FEEDBACK_ENABLED='false'):self.assertIsNone(w.accept(self.event()))
    def test_binding_capture_restart_and_event_id_dedup(self):
        snapshot=json.loads(self.box.get(self.p['id'])['snapshot'])
        self.assertEqual(snapshot['weekly_draft_id'],DRAFT)
        original=copy.deepcopy(self.remote.pages)
        e=self.enqueue();b=self.event();b['event_id']='E2';self.box.enqueue_event(w.accept(b))
        self.box.poll();o.Outbox(self.box.path,self.remote).poll()
        self.assertEqual(self.pending(),[])
        self.assertEqual(self.remote.append_count,{self.p['id']:1,DRAFT:1})
        self.assertEqual(self.remote.pages,original)
        content=w.block_text(self.remote.blocks[DRAFT][0])
        self.assertIn(e['text'],content);self.assertIn('Pending incorporation',content)
        replies=[d for m,d in self.remote.calls if m=='chat.postMessage' and 'thread_ts' in d]
        self.assertEqual(len(replies),1);self.assertEqual(replies[0]['thread_ts'],self.parent)
    def test_timeout_after_append_is_reconciled_without_duplicate(self):
        self.remote.fail_destination=DRAFT;self.remote.accept_timeout=True
        self.enqueue();self.box.poll();self.assertEqual(self.pending()[0]['progress']['draft'],'writing')
        self.remote.fail_destination=None
        o.Outbox(self.box.path,self.remote).poll()
        self.assertEqual(self.pending(),[]);self.assertEqual(self.remote.append_count[DRAFT],1)
        self.assertEqual(self.remote.append_count[self.p['id']],1)
    def test_unknown_write_is_held_and_warned_once(self):
        self.remote.fail_destination=DRAFT;self.enqueue();self.box.poll()
        self.remote.fail_destination=None;self.box.poll();self.box.poll()
        self.assertEqual(self.remote.append_count[DRAFT],1)
        self.assertIn('requires_reconciliation',self.pending()[0]['error'])
        notices=[d for m,d in self.remote.calls if m=='chat.postMessage' and 'thread_ts' in d]
        self.assertEqual(len(notices),1);self.assertIn('pending',notices[0]['text'])
    def test_existing_post_resolves_once_and_rejects_changed_week(self):
        # Pre-feature snapshots have no link; only an unambiguous exact Week match may bind.
        with self.box.db() as db:
            r=json.loads(self.box.get(self.p['id'])['snapshot']);r.pop('weekly_draft_id')
            db.execute('UPDATE outbox_delivery SET snapshot=? WHERE id=?',(json.dumps(r),self.p['id']))
        self.remote.duplicate=True;self.enqueue();self.box.poll();self.assertEqual(self.remote.blocks,{})
        self.remote.duplicate=False;self.box.poll();self.assertEqual(self.pending(),[])
        self.remote.pages[DRAFT]['properties']['Week']['date']['start']='2026-11-09'
        self.enqueue(ts='1793133459.5');self.box.poll()
        self.assertEqual(self.remote.append_count[DRAFT],1)
    def test_future_post_refuses_ambiguous_week(self):
        self.remote.duplicate=True;p=weekly(n=102);self.remote.pages[p['id']]=p
        with self.assertRaisesRegex(ValueError,'ambiguous'):self.box.post(p)
        self.assertIsNone(self.box.get(p['id']))
    def test_pause_preserves_accepted_work(self):
        self.enqueue()
        with patch.dict(os.environ,WEEKLY_FEEDBACK_ENABLED='false'):self.box.poll()
        self.assertEqual(len(self.pending()),1);self.assertEqual(self.remote.blocks,{})
    def test_bound_draft_does_not_requery_or_cross_source(self):
        self.enqueue();self.remote.duplicate=True;self.box.poll();self.assertEqual(self.pending(),[])
        self.remote.pages[DRAFT]['parent']['data_source_id']=o.SOURCE
        self.enqueue(ts='1793133459.6');self.box.poll();self.assertEqual(self.remote.append_count[DRAFT],1)
    def test_ack_timeout_does_not_repost_on_restart(self):
        self.enqueue();self.remote.fail=[TimeoutError()];self.box.poll();self.box.poll()
        replies=[d for m,d in self.remote.calls if m=='chat.postMessage' and 'thread_ts' in d]
        self.assertEqual(len(replies),1)
        self.assertEqual(sum('Captured your feedback' in d['text'] for d in replies),1)
        self.assertEqual(self.remote.append_count[DRAFT],1)
    def test_attachment_reference_and_long_text(self):
        self.enqueue(files=[{'id':'F1'}]);self.box.poll()
        self.assertIn('Attachments were not captured',w.block_text(self.remote.blocks[DRAFT][0]))
        self.enqueue(ts='1793133459.7',text='<@'+o.BOT+'> '+('a'*12001));self.box.poll()
        self.assertIn('too_long',self.pending()[0]['error']);self.assertEqual(self.remote.append_count[DRAFT],1)

if __name__=='__main__':unittest.main()
