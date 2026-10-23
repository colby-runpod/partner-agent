import copy
import datetime as dt
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

import outbox as o


def page(n=1, kind='supply_lead', channel='C0CAL8C08RK', **fields):
    props = {'Status': {'type':'select','select':{'name':'Queued'}},
             'Kind': {'type':'select','select':{'name':kind}},
             'Related Intake': {'type':'relation','relation':[]}}
    for k,v in dict({'Channel ID':channel,'Body':'*Runpod* <https://example.com|Tracker> `person@example.com`',
                     'Mention IDs':'UOWNER','Thread TS':'','Message TS':''}, **fields).items():
        props[k] = dict(type='rich_text', **o.rich(v))
    return {'id':str(uuid.UUID(int=n)), 'properties':props, 'n':n}


class Remote:
    def __init__(self, pages):
        self.pages={p['id']:copy.deepcopy(p) for p in pages}
        self.calls=[]; self.writes=[]; self.fail=[]; self.patch_fail=0; self.bot=False; self.identity=o.BOT
    def get(self,id): return copy.deepcopy(self.pages[id])
    def patch(self,id,props):
        if self.patch_fail:
            self.patch_fail-=1; raise TimeoutError()
        self.writes.append((id,copy.deepcopy(props)))
        for k,v in props.items():
            self.pages[id]['properties'][k]=dict(type=next(iter(v)),**v)
    def query(self,f):
        return [copy.deepcopy(p) for p in self.pages.values() if
                (p.get('n')==f.get('unique_id',{}).get('equals') if f['property']=='Post ID' else
                 o.prop(p,'Status')==f['select']['equals'])]
    def slack(self,method,data):
        self.calls.append((method,copy.deepcopy(data)))
        if method=='auth.test': return {'ok':True,'user_id':self.identity,'team_id':'T1'}
        if method=='chat.postMessage':
            if self.fail: raise self.fail.pop(0)
            return {'ok':True,'ts':str(len(self.calls))+'.001'}
        if method=='chat.getPermalink': return {'permalink':'https://slack.example/message'}
        if method=='users.info': return {'user':{'is_bot':self.bot}}
        return {'ok':True}


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,SLACK_TEAM_ID='T1'); self.env.start(); self.addCleanup(self.env.stop)
    def setup_box(self,pages):
        self.remote=Remote(pages); self.box=o.Outbox(self.temp.name+'/ledger.db',self.remote); return self.box
    def posts(self): return [p for method,p in self.remote.calls if method=='chat.postMessage']
    def test_cutoff_excludes_old_equal_and_unknown_creation_times(self):
        pages=[page(n) for n in range(1,6)]
        for p,t in zip(pages,['2026-10-23T15:59:59Z','2026-10-23T16:00:00Z',None,'invalid','2026-10-23T10:00:01-06:00']):
            if t: p['created_time']=t
        with patch.dict(os.environ,OUTBOX_CREATED_AFTER='2026-10-23T16:00:00Z'):
            self.setup_box(pages).poll()
            self.box.poll()
        self.assertEqual(len(self.posts()),1)
        for p in pages[:-1]: self.assertEqual(self.remote.pages[p['id']],p)
        self.assertEqual({id for id,_ in self.remote.writes},{pages[-1]['id']})

    def test_cutoff_invalid_configuration_fails_closed(self):
        for value in ('','2026-10-23','2026-10-23T16:00:00','bad'):
            with patch.dict(os.environ,OUTBOX_CREATED_AFTER=value):
                with self.assertRaises(ValueError): self.setup_box([])

    def test_cutoff_rechecked_before_sending(self):
        stale=page(); stale['created_time']='2026-10-23T16:00:01Z'
        fresh=copy.deepcopy(stale); fresh['created_time']='2026-10-22T16:00:00Z'
        with patch.dict(os.environ,OUTBOX_CREATED_AFTER='2026-10-23T16:00:00Z'):
            box=self.setup_box([fresh]); box.post(stale)
        self.assertEqual(self.posts(),[])
        self.assertEqual(self.remote.writes,[])

    def test_permalink_uses_query_parameters(self):
        with patch.dict(os.environ,SLACK_BOT_TOKEN='test'), patch.object(o.Remote,'call',return_value={}) as call:
            o.Remote().slack('chat.getPermalink',{'channel':'C1','message_ts':'123.456'})
        args=call.call_args.args
        self.assertEqual(args[1],'chat.getPermalink?channel=C1&message_ts=123.456')
        self.assertEqual(args[2:4],('GET',None))

    def test_development_update_posts_once(self):
        p = page(kind='other', channel='C0C7R970C5T')
        box = self.setup_box([p]); box.poll(); box.poll()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.posts()[0]['channel'], 'C0C7R970C5T')

    def test_invitation_buttons_still_require_ask_partners(self):
        p = page(kind='invitation_queue', channel='C0C7R970C5T')
        p['properties']['Related Intake']['relation'] = [{'id':str(uuid.UUID(int=100))}]
        with self.assertRaisesRegex(ValueError, 'Invitations must be in ask-partners'):
            o.payload(o.row(p))

    def test_test_ids_leave_real_queue_untouched(self):
        real, test = page(1), page(2)
        with patch.dict(os.environ, OUTBOX_TEST_IDS=test['id']):
            self.setup_box([real,test]).poll()
            self.box.poll()
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(self.remote.pages[real['id']],real)
        self.assertEqual({id for id,_ in self.remote.writes},{test['id']})

    def test_invalid_test_ids_fail_closed(self):
        for value in ('', 'not-a-page', page()['id']+','):
            with patch.dict(os.environ, OUTBOX_TEST_IDS=value):
                with self.assertRaises(ValueError): self.setup_box([page()])

    def test_test_ids_block_previous_receipts_and_pickup(self):
        real, test = page(1), page(2)
        self.setup_box([real,test]).poll()
        event=self.event(real)
        self.remote.writes=[]; self.remote.calls=[]
        with patch.dict(os.environ, OUTBOX_TEST_IDS=test['id']):
            box=o.Outbox(self.box.path,self.remote)
            box.sync(box.get(real['id']))
            box.event(event)
        self.assertEqual(self.remote.writes,[])
        self.assertFalse(any(m.startswith('chat.') for m,_ in self.remote.calls))

    def test_thirteen_rows_threaded_once_across_restart(self):
        pages=[page(1,'invitation_queue','C0C7HG8KYFL')]
        for n in range(2,12):
            p=page(n,'invitation_queue','C0C7HG8KYFL',**{'Thread TS':'parent:OUT-1'})
            p['properties']['Related Intake']['relation']=[{'id':str(uuid.UUID(int=100+n))}]
            pages.append(p)
        pages += [page(12),page(13,'event_request','C0CAKA6PW8K')]
        box=self.setup_box(pages); box.poll()
        self.assertEqual(len(self.posts()),13)
        self.assertNotIn('blocks',self.posts()[0])
        ts=o.prop(self.remote.pages[pages[0]['id']],'Message TS')
        for posted in self.posts()[1:11]:
            self.assertEqual(posted['thread_ts'],ts)
            self.assertEqual(len(posted['blocks'][-1]['elements']),3)
        for p in self.remote.pages.values():
            self.assertEqual(o.prop(p,'Status'),'Posted')
            self.assertTrue(o.prop(p,'Message TS'))
        o.Outbox(box.path,self.remote).poll()
        self.assertEqual(len(self.posts()),13)
    def test_invalid_channel_future_cancelled_and_existing_receipt(self):
        bad=page(1,channel='C000000000'); future=page(2); cancelled=page(3); old=page(4,**{'Message TS':'10.5'})
        future['properties']['Not Before']={'type':'date','date':{'start':'2099-01-01T00:00:00Z'}}
        cancelled['properties']['Status']['select']['name']='Cancelled'
        self.setup_box([bad,future,cancelled,old]).poll()
        self.assertEqual(self.posts(),[])
        self.assertEqual(o.prop(self.remote.pages[bad['id']],'Error'),'channel not allowed')
        self.assertEqual(o.prop(self.remote.pages[future['id']],'Status'),'Queued')
    def test_body_verbatim_mentions_and_unfurls(self):
        p=page(); self.setup_box([p]).poll(); sent=self.posts()[0]
        self.assertEqual(sent['text'],'<@UOWNER>\n'+o.prop(p,'Body'))
        self.assertTrue(sent['mrkdwn']);self.assertFalse(sent['unfurl_links']);self.assertFalse(sent['unfurl_media'])
    def test_definitive_rejection_retries_once(self):
        p=page(); self.setup_box([p]); self.remote.fail=[o.Rejected('not_in_channel')]*2
        self.box.poll(); self.box.poll()
        self.assertEqual(len(self.posts()),2)
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Failed')
    def test_timeout_never_reposts(self):
        p=page();self.setup_box([p]);self.remote.fail=[TimeoutError()]
        self.box.poll();self.box.poll()
        self.assertEqual(len(self.posts()),1)
        self.assertIn('Ambiguous',o.prop(self.remote.pages[p['id']],'Error'))
    def test_notion_writeback_failure_does_not_repost(self):
        p=page();self.setup_box([p]);self.remote.patch_fail=1
        self.box.poll();self.box.poll()
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Posted')
    def event(self,p,user=o.COLBY,**extra):
        return dict(key='e1',kind='outbox_event',channel=o.prop(p,'Channel ID'),
                    message_ts=self.box.get(p['id'])['ts'],user=user,reaction='white_check_mark',**extra)
    def test_handoff_reaction_only_changes_outbox(self):
        p=page();self.setup_box([p]).poll();self.remote.writes=[]
        self.box.event(self.event(p));self.box.event(self.event(p))
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Confirmed')
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Confirmed By'),o.COLBY)
        self.assertEqual({id for id,_ in self.remote.writes},{p['id']})
    def test_bot_and_colby_reply_ignored_human_reply_confirms(self):
        p=page();self.setup_box([p]).poll()
        self.box.event(self.event(p,user=o.BOT))
        self.remote.bot=True;self.box.event(self.event(p,user='UOTHER'))
        self.remote.bot=False;self.box.event(self.event(p,reply=True))
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Posted')
        self.box.event(self.event(p,user='UOTHER',reply=True))
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Confirmed')
    def invitation(self):
        p=page(kind='invitation_queue',channel='C0C7HG8KYFL');t=page(99)
        t['parent']={'data_source_id':o.INTAKE}
        p['properties']['Related Intake']['relation']=[{'id':t['id']}]
        self.setup_box([p]);self.remote.pages[t['id']]=t;self.box.post(p);self.remote.writes=[]
        e=self.event(p);e.update(kind='outbox_action',row_id=p['id'],action='outbox_approve')
        return p,t,e
    def test_only_colby_can_approve_narrow_intake_patch(self):
        p,t,e=self.invitation();self.box.event(dict(e,user='UNOTCOLBY'))
        self.assertEqual(self.remote.writes,[])
        self.assertEqual(self.remote.calls[-1][0],'chat.postEphemeral')
        self.box.event(e)
        writes=[props for id,props in self.remote.writes if id==t['id']]
        self.assertEqual(len(writes),1)
        self.assertEqual(set(writes[0]),{'Invitation Decision','Invitation Approved By','Invitation Approved Date'})
        self.assertEqual(writes[0]['Invitation Decision']['select']['name'],'Approved')
        update=[p for m,p in self.remote.calls if m=='chat.update'][-1]
        self.assertEqual(update['blocks'],[])
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Confirmed')
        self.box.event(dict(e,action='outbox_decline'))
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Decision'),'Approve')
    def test_call_first_no_intake_write(self):
        p,t,e=self.invitation();self.box.event(dict(e,action='outbox_call'))
        self.assertFalse(any(id==t['id'] for id,_ in self.remote.writes))
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Decision'),'Call first')
    def test_wrong_intake_source_is_not_written(self):
        p,t,e=self.invitation();self.remote.pages[t['id']]['parent']['data_source_id']='wrong'
        with self.assertRaises(ValueError):self.box.event(e)
        self.assertFalse(self.remote.writes)
    def test_wrong_bot_identity_prevents_posting(self):
        self.setup_box([page()]);self.remote.identity='UCOLBY'
        with self.assertRaises(ValueError):self.box.poll()
        self.assertEqual(self.posts(),[])
    def test_decline_action_confirms_outbox_but_declines_intake(self):
        p,t,e=self.invitation();self.box.event(dict(e,action='outbox_decline'))
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Confirmed')
        self.assertEqual(o.prop(self.remote.pages[t['id']],'Invitation Decision'),'Declined')
    def test_cancelled_after_post_has_no_decision_writes(self):
        p,t,e=self.invitation();self.remote.pages[p['id']]['properties']['Status']['select']['name']='Cancelled'
        self.box.event(e);self.assertEqual(self.remote.writes,[])
    def test_stale_receipt_prevents_send(self):
        p=page();self.setup_box([p]);self.remote.pages[p['id']]['properties']['Message TS']=dict(type='rich_text',**o.rich('99.1'))
        self.box.post(p);self.assertEqual(self.posts(),[])
    def test_invalid_time_parent_and_insecure_links_fail_without_send(self):
        p=page();p['properties']['Not Before']={'type':'date','date':{'start':'2026-01-01'}}
        p2=page(2,**{'Thread TS':'parent:bad'})
        p3=page(3,**{'Body':'<http://example.com|bad>'})
        self.setup_box([p,p2,p3]).poll();self.assertEqual(self.posts(),[])
        for item in self.remote.pages.values():self.assertEqual(o.prop(item,'Status'),'Failed')
    def test_event_filter_requires_owned_message_and_correct_workspace(self):
        p=page();self.setup_box([p]).poll();e=self.event(p)
        cfg={'outbox_enabled':True,'team_id':'T1','app_id':'A1'}
        body={'team_id':'T1','api_app_id':'A1','event_id':'EV1','event':{'type':'reaction_added','user':o.COLBY,
              'reaction':'eyes','item':{'type':'message','channel':e['channel'],'ts':e['message_ts']}}}
        with patch.dict(os.environ,CHAT_DB=self.box.path):
            self.assertIsNotNone(o.accept(body,cfg))
            self.assertIsNone(o.accept(dict(body,team_id='T2'),cfg))
            body['event']['item']['ts']='666.1'
            self.assertIsNone(o.accept(body,cfg))
    def test_button_filter_requires_valid_action_and_timestamp(self):
        cfg={'outbox_enabled':True,'team_id':'T1','app_id':'A1'}
        p={'team':{'id':'T1'},'api_app_id':'A1','type':'block_actions','channel':{'id':'C0C7HG8KYFL'},
           'user':{'id':o.COLBY},'message':{'ts':'1.1'},'actions':[{'action_id':'outbox_approve','value':str(uuid.UUID(int=1)),'action_ts':'2.1'}]}
        self.assertIsNotNone(o.action(p,cfg))
        p['actions'][0]['action_ts']=''
        self.assertIsNone(o.action(p,cfg))
    def test_durable_event_retry(self):
        p=page();self.setup_box([p]).poll();e=self.event(p)
        self.box.enqueue_event(e);self.box.enqueue_event(e)
        self.remote.patch_fail=1;self.box.poll();self.box.poll()
        self.assertEqual(o.prop(self.remote.pages[p['id']],'Status'),'Confirmed')
        self.assertEqual(len(self.posts()),1)

if __name__=='__main__':unittest.main()
