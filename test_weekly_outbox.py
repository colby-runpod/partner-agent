import copy
import os
import unittest
from unittest.mock import patch
import outbox as o
from test_outbox import page, Remote
import tempfile


def weekly(n=101, **fields):
    p=page(n,kind='other',channel=o.WEEKLY_CHANNEL,**{'Mention IDs':'',**fields})
    p['properties']['Name']={'type':'title','title':[{'text':{'content':'weekly-update-summary:2026-11-02'}}]}
    p['created_time']='2026-10-27T18:00:00Z'
    return p

class WeeklyOutbox(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=patch.dict(os.environ,SLACK_TEAM_ID='T1',WEEKLY_OUTBOX_ENABLED='true',OUTBOX_TEST_IDS=page(22)['id'],OUTBOX_CREATED_AFTER='2026-10-23T19:38:28Z');p.start();self.addCleanup(p.stop)
    def box(self,pages):
        self.remote=Remote(pages);return o.Outbox(self.tmp.name+'/db',self.remote)
    def posts(self):return [p for m,p in self.remote.calls if m=='chat.postMessage']
    def test_scoped_delivery_verbatim_and_restart_deduplication(self):
        p=weekly(Body='*Weekly update*\nProgress & next steps <https://example.com|Details>')
        unrelated=page(99);unrelated['created_time']=p['created_time']
        b=self.box([p,unrelated]);b.poll();o.Outbox(b.path,self.remote).poll()
        self.assertEqual(len(self.posts()),1);sent=self.posts()[0]
        self.assertEqual(sent['text'],o.prop(p,'Body'));self.assertNotIn('thread_ts',sent);self.assertNotIn('blocks',sent)
        self.assertEqual(sent['parse'],'none');self.assertFalse(sent['link_names'])
        saved=self.remote.pages[p['id']];self.assertEqual(o.prop(saved,'Status'),'Posted')
        self.assertTrue(o.prop(saved,'Message TS'));self.assertEqual(saved['properties']['Permalink']['url'],'https://slack.example/message')
        self.assertEqual(self.remote.pages[unrelated['id']],unrelated)
    def test_no_mentions_threads_or_other_kinds_and_valid_monday(self):
        invalid=[weekly(**{'Mention IDs':'UOWNER'}),weekly(**{'Thread TS':'123.4'}),weekly(**{'Thread TS':'parent:OUT-1'}),
                 weekly(Body='<@UOWNER>'),weekly(Body='<!here>'),weekly(Body='<!subteam^S1|group>')]
        p=weekly();p['properties']['Kind']['select']['name']='daily_digest';invalid.append(p)
        for title in ('wrong','weekly-update-summary:2026-11-03','weekly-update-summary:2026-02-30'):
            p=weekly();p['properties']['Name']['title'][0]['text']['content']=title;invalid.append(p)
        for i,p in enumerate(invalid):
            p['id']=page(i+200)['id']
        b=self.box(invalid);b.poll();self.assertEqual(self.posts(),[])
        for p in invalid:
            saved=self.remote.pages[p['id']];self.assertEqual(o.prop(saved,'Status'),'Failed');self.assertTrue(o.prop(saved,'Error'))
    def test_writeback_retry_does_not_post_twice(self):
        p=weekly();b=self.box([p]);self.remote.patch_fail=1;b.poll()
        self.assertEqual(b.get(p['id'])['state'],'sync')
        o.Outbox(b.path,self.remote).poll();self.assertEqual(len(self.posts()),1)
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Posted')
    def test_timeout_is_failed_and_held(self):
        p=weekly();b=self.box([p]);self.remote.fail=[TimeoutError()];b.poll();b.poll()
        self.assertEqual(len(self.posts()),1);self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Failed')
        self.assertIn('Ambiguous',o.prop(self.remote.pages[p['id']],'Error'))
    def test_flag_cutoff_and_future_due(self):
        p=weekly();b=self.box([p])
        with patch.dict(os.environ,WEEKLY_OUTBOX_ENABLED='false'):o.Outbox(b.path,self.remote).poll()
        self.assertEqual(self.posts(),[])
        p['created_time']='2026-10-22T00:00:00Z';self.remote.pages[p['id']]=p;b.poll();self.assertEqual(self.posts(),[])
        p=weekly(**{'Not Before':'2099-11-02T09:00:00-07:00'});self.remote.pages[p['id']]=p;b.poll();self.assertEqual(self.posts(),[])
    def test_changed_body_revalidated_and_feedback_ignored(self):
        p=weekly();b=self.box([p]);fresh=copy.deepcopy(p);fresh['properties']['Body']=dict(type='rich_text',**o.rich('<!channel>'))
        self.remote.pages[p['id']]=fresh;b.post(p);self.assertEqual(self.posts(),[])
        b.poll();self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Failed')
        cfg={'outbox_enabled':True,'team_id':'T1','app_id':'A1'}
        body=dict(team_id='T1',api_app_id='A1',event_id='E1',event=dict(type='message',user='U1',channel=o.WEEKLY_CHANNEL,ts='2.1',thread_ts='1.1'))
        with patch.object(o,'owns') as owns:
            self.assertIsNone(o.accept(body,cfg));owns.assert_not_called()
