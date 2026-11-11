import copy
import datetime as dt
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch, Mock
import intake_alignment as a
import outbox
import partner_sources
from test_outbox import page, Remote


def prop_page(id, **values):
    p={'id':id,'url':'https://www.notion.so/'+id,'parent':{'data_source_id':a.INTAKE},'properties':{}}
    for k,v in values.items():
        p['properties'][k]=dict(type='select',select={'name':v}) if k in ('Status','Forward To','Source') else dict(type='rich_text',**a.rich(v))
    return p


class AlignedRemote(Remote):
    def __init__(self,pages):
        super().__init__(pages);self.history=[];self.activity=[]
    def query(self,f):
        if f['property']=='Related Intake':
            return [copy.deepcopy(p) for p in self.pages.values() if f['relation']['contains'] in (a.value(p,'Related Intake') or [])]
        return super().query(f)
    def notion(self,method,path,data=None):
        if method=='POST' and path.endswith('/query'):
            if a.ACTIVITY in path:
                if 'and' in data.get('filter',{}): return {'results':self.activity}
                needle=data['filter']['rich_text']['equals']
                return {'results':[p for p in self.activity if a.value(p,'Source Ref')==needle]}
            return {'results':list(self.pages.values())}
        if method=='POST' and path=='pages':
            p={'id':str(uuid.uuid4()),'properties':data['properties']};self.activity.append(p);return p
        raise AssertionError((method,path))
    def slack(self,method,data):
        if method=='conversations.members': return {'members':['U0ACSTWBYHG'],'response_metadata':{}}
        if method=='users.info': return {'user':{'id':data['user'],'deleted':False}}
        if method=='conversations.history':return {'messages':self.history,'response_metadata':{}}
        return super().slack(method,data)


class Contract(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,INTAKE_ALIGNMENT_ENABLED='true',SLACK_TEAM_ID='T1');self.env.start();self.addCleanup(self.env.stop)
        state=patch.dict(a._GUIDE_STATE,blocked=False);state.start();self.addCleanup(state.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.iid=str(uuid.UUID(int=1000))
    def setup(self,n=1):
        p=page(n,**{'Title':'pipeline:'+self.iid+':supply_lead:source','Body':'<https://slack.com/archives/C0BQKEQLSKH/p1001|Original request>', 'Mention IDs':'U0ACSTWBYHG'})
        p['properties']['Title']={'type':'title',**a.title('pipeline:'+self.iid+':supply_lead:source')}
        p['created_time']=dt.datetime.now(dt.timezone.utc).isoformat()
        p['properties']['Related Intake']['relation']=[{'id':self.iid}]
        intake=prop_page(self.iid,**{'Forward To':'Supply Team','Status':'New','Intake Ref':'tpa:C0BQKEQLSKH:100.1'})
        self.remote=AlignedRemote([p,intake]);self.box=outbox.Outbox(self.tmp.name+'/db',self.remote)
        return p
    def posts(self):return [d for m,d in self.remote.calls if m=='chat.postMessage']
    def test_single_delivery_receipt_intake_activity_and_source_reaction(self):
        p=self.setup();self.box.post(p);self.box.post(p)
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(a.value(self.remote.get(p['id']),'Status'),'Posted')
        self.assertEqual(a.value(self.remote.get(self.iid),'Status'),'Actioned')
        self.assertEqual(len(self.remote.activity),1)
        self.assertTrue(any(m=='reactions.add' and d['name']=='white_check_mark' for m,d in self.remote.calls))
    def test_pipeline_dm_receipt_completes_without_source_reaction(self):
        p=self.setup()
        self.remote.patch(self.iid,{'Intake Ref':a.rich('slack:D0BFPB446H3:100.1')})
        original=self.remote.slack
        def slack(method,data):
            if method.startswith('reactions.') and data.get('channel','').startswith('D'):
                raise RuntimeError('channel_not_found')
            return original(method,data)
        self.remote.slack=slack
        self.box.post(p);self.box.post(p)
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(self.box.get(p['id'])['state'],'posted')
        self.assertEqual(a.value(self.remote.get(self.iid),'Status'),'Actioned')
        self.assertEqual(len(self.remote.activity),1)
        self.assertFalse(any(m.startswith('reactions.') and d.get('channel','').startswith('D') for m,d in self.remote.calls))

    def test_duplicate_queue_row_adopts_same_receipt(self):
        p=self.setup();self.box.post(p)
        duplicate=copy.deepcopy(p);duplicate['id']=str(uuid.UUID(int=2));self.remote.pages[duplicate['id']]=copy.deepcopy(duplicate)
        self.box.post(duplicate)
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(a.value(self.remote.get(p['id']),'Message TS'),a.value(self.remote.get(duplicate['id']),'Message TS'))
    def test_historical_own_post_adopted_not_resent(self):
        p=self.setup();self.remote.history=[{'user':outbox.BOT,'ts':'44.1','text':a.value(p,'Body')}]
        self.box.post(p)
        self.assertEqual(self.posts(),[])
        self.assertEqual(a.value(self.remote.get(p['id']),'Message TS'),'44.1')
    def test_other_users_matching_source_does_not_count(self):
        p=self.setup();self.remote.history=[{'user':'UOTHER','ts':'44.1','text':a.value(p,'Body')}]
        self.box.post(p);self.assertEqual(len(self.posts()),1)
    def test_stale_row_held_and_reported(self):
        p=self.setup();p['created_time']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=73)).isoformat()
        self.box.post(p);self.assertFalse(self.posts());self.assertIn('not Flagged',a.value(self.remote.get(p['id']),'Error'))
        self.assertEqual(a.value(self.remote.get(p['id']),'Status'),'Queued')
    def test_delayed_flagged_handoff_posts_with_label_and_owner_backfill(self):
        p=self.setup();p['created_time']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=80)).isoformat()
        p['properties']['Mention IDs']=dict(type='rich_text',**a.rich(''))
        self.remote.pages[p['id']]=copy.deepcopy(p)
        self.remote.patch(self.iid,{'Status':{'select':{'name':'Flagged'}}})
        self.box.post(p);self.box.post(self.remote.get(p['id']))
        self.assertEqual(len(self.posts()),1)
        self.assertTrue(self.posts()[0]['text'].startswith('Delayed handoff, queued '))
        self.assertIn('<@U0ACSTWBYHG>',self.posts()[0]['text'])
        self.assertEqual(a.value(self.remote.get(p['id']),'Mention IDs'),'U0ACSTWBYHG')
        self.assertEqual(a.value(self.remote.get(self.iid),'Status'),'Actioned')
        self.assertEqual(a.value(self.remote.activity[0],'Guide Version'),'2026-11-10.1')

    def test_own_queue_creation_sets_current_route_owner_ids(self):
        for route,(_,owners) in a.ROUTES.items():
            notion=Mock();notion._req.return_value={'id':'new'}
            intake=prop_page(self.iid,**{'Forward To':route})
            with patch.object(a,'capture',return_value=intake),patch.object(a,'set_route',return_value=route),patch.object(a,'query',return_value=iter([])),patch.object(a,'audit'):
                a.queue_handoff(notion,{'completion_source':{'ts':'100.1'},'completion_route':route,'channel':a.ROUTES[route][0],'key':'key','text':'Lead'})
            props=notion._req.call_args.args[2]['properties']
            self.assertEqual(a.value({'properties':props},'Mention IDs'),','.join(owners))

    def test_delayed_already_actioned_adopts_existing_post(self):
        p=self.setup();p['created_time']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=80)).isoformat()
        self.remote.pages[p['id']]=copy.deepcopy(p)
        self.remote.patch(self.iid,{'Status':{'select':{'name':'Actioned'}}})
        self.remote.history=[{'user':outbox.BOT,'ts':'44.1','text':a.value(p,'Body')}]
        self.box.post(p)
        self.assertFalse(self.posts());self.assertEqual(self.box.get(p['id'])['ts'],'44.1')

    def test_delayed_route_mismatch_stays_queued(self):
        p=self.setup();p['created_time']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=80)).isoformat()
        self.remote.pages[p['id']]=copy.deepcopy(p)
        self.remote.patch(self.iid,{'Status':{'select':{'name':'Flagged'}},'Forward To':{'select':{'name':'Startup Program'}}})
        self.box.post(p)
        self.assertFalse(self.posts())
        self.assertEqual(a.value(self.remote.get(p['id']),'Status'),'Queued')
        self.assertIn('does not match',a.value(self.remote.get(p['id']),'Error'))

    def test_priority_event_then_oldest_first(self):
        pages=[]
        for n,created in [(92,'2026-11-04T00:00:00Z'),(166,'2026-11-09T00:00:00Z'),(129,'2026-11-06T00:00:00Z')]:
            p=self.setup(n);p['created_time']=created;p['properties']['Post ID']={'type':'unique_id','unique_id':{'number':n}};pages.append(p)
        self.remote.query=lambda f:pages
        with patch.object(self.box,'post') as post,patch.object(a,'refresh_guide'):
            self.box.poll()
        self.assertEqual([c.args[0]['properties']['Post ID']['unique_id']['number'] for c in post.call_args_list],[166,92,129])

    def test_test_rows_never_eligible_even_with_allowed_title(self):
        p=self.setup(12);p['properties']['Post ID']={'type':'unique_id','unique_id':{'number':12}}
        self.assertFalse(self.box.eligible(p))
        p=self.setup(13);p['properties']['Title']={'type':'title',**a.title('TEST event post')}
        self.assertFalse(self.box.eligible(p))

    def test_source_identity_ignores_slack_hostname_and_gmail_folder(self):
        self.assertEqual(a.source_links('https://runpod.enterprise.slack.com/archives/C123/p1001'),a.source_links('https://runpod-team.slack.com/archives/C123/p1001'))
        self.assertEqual(a.source_links('https://mail.google.com/mail/u/0/#inbox/abc123'),a.source_links('https://mail.google.com/mail/u/1/#all/abc123'))

    def test_gmail_source_duplicate_is_adopted(self):
        p=self.setup();p['properties']['Body']={'type':'rich_text',**a.rich('<https://mail.google.com/mail/u/0/#all/abc123|Original email>')}
        self.remote.pages[p['id']]=copy.deepcopy(p)
        self.remote.history=[{'user':outbox.BOT,'ts':'44.1','text':a.value(p,'Body')}]
        self.box.post(p)
        self.assertFalse(self.posts());self.assertEqual(self.box.get(p['id'])['ts'],'44.1')

    def test_park_route_mismatch_missing_relation_fail_before_delivery(self):
        for mode in ('park','mismatch','no_record'):
            with self.subTest(mode=mode):
                p=self.setup()
                if mode=='park':self.remote.patch(self.iid,{'Next Step Notes':a.rich('Parked by Colby yesterday')})
                if mode=='mismatch':self.remote.patch(self.iid,{'Forward To':{'select':{'name':'Events/Sponsorship'}}})
                if mode=='no_record':p['properties']['Related Intake']['relation']=[]
                self.box.post(p);self.assertFalse(self.posts())
    def test_pickup_accepts_owner_not_bot_or_arbitrary_user_or_reply(self):
        p=self.setup();self.box.post(p);d=self.box.get(p['id']);ts=d['ts']
        e={'kind':'outbox_event','message_ts':ts,'channel':'C0CAL8C08RK','user':'UOTHER','reaction':'eyes'}
        self.box.event(e);self.assertIsNone(self.box.get(p['id'])['decision'])
        e['user']='U0ACSTWBYHG';e['reply']=True;self.box.event(e);self.assertIsNone(self.box.get(p['id'])['decision'])
        e['reply']=False;e['reaction']='x';self.box.event(e)
        self.assertEqual(a.value(self.remote.get(p['id']),'Status'),'Declined')
        self.assertEqual(a.value(self.remote.get(self.iid),'Status'),'Flagged')
    def test_activity_identity_preserves_separate_events(self):
        self.setup()
        for key in ('one','one','two'):a.audit(self.remote.notion,key,'Card decision','Park',intake=self.iid)
        self.assertEqual(len(self.remote.activity),2)
    def test_distinct_submissions_same_contact_are_not_collapsed(self):
        r={'company':'X','email':'same@example.com'}
        e={'key':'a','channel':'C123','ts':'100.1'}
        self.assertNotEqual(partner_sources.handoff_key(r,e,'Supply Team'),partner_sources.handoff_key(r,dict(e,ts='100.2'),'Supply Team'))
    def test_startup_and_vc_same_destination_dedupe_but_different_owners(self):
        cfg={'routing_enabled':True};e={'key':'a','channel':'C123','ts':'100.1'}
        jobs=[partner_sources.handoff({'company':'X','route':r},e,cfg) for r in ('Startup Program','VC/Incubator')]
        self.assertIn('U0B9ZRF3JUX',jobs[0]['text']);self.assertIn('U0CE06U7K52',jobs[1]['text'])
        self.assertEqual(jobs[0]['key'],jobs[1]['key'])
    def test_low_score_is_warning_legal_citation_and_test_block(self):
        cfg={'routing_enabled':True};e={'key':'a','channel':'C123','ts':'100.1'}
        r={'company':'X','route':'Supply Team','score':1,'screening':{'tier':'DQ','risk':'policy_review','risk_evidence':'unknown country'}}
        self.assertIsNotNone(partner_sources.handoff(r,e,cfg))
        r['screening']['risk_evidence']='Sanctions finding https://example.com/evidence'
        self.assertIsNone(partner_sources.handoff(r,e,cfg))
        self.assertIsNone(partner_sources.handoff({'company':'X','route':'Supply Team'},dict(e,synthetic_test=True),cfg))
    def test_wrong_owner_content_is_rejected(self):
        p=self.setup();p['properties']['Mention IDs']={'type':'rich_text',**a.rich('U0B9ZRF3JUX')}
        self.box.post(p);self.assertFalse(self.posts())
        self.assertIn('mentions',a.value(self.remote.get(p['id']),'Error'))
    def test_writeback_failure_never_reposts(self):
        p=self.setup()
        original=self.remote.patch
        failed=[False]
        def fail_intake(id,props):
            if id==self.iid and not failed[0]:
                failed[0]=True;raise TimeoutError()
            return original(id,props)
        self.remote.patch=fail_intake
        with self.assertRaises(TimeoutError):self.box.post(p)
        self.assertEqual(self.box.get(p['id'])['state'],'sync')
        self.box.sync(self.box.get(p['id']))
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(len(self.remote.activity),1)
    def test_ambiguous_send_does_not_retry(self):
        p=self.setup();self.remote.fail=[TimeoutError()]
        with self.assertRaises(TimeoutError):self.box.post(p)
        self.box.post(p)
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(a.value(self.remote.get(p['id']),'Status'),'Failed')
    def test_pipeline_request_is_pending_not_draft_created(self):
        self.setup();notion=Mock();notion._req.return_value=self.remote.get(self.iid)
        with patch.object(a,'audit') as log:
            a.request_pipeline(notion,self.iid,'request-1','Draft a reply','https://slack.com/archives/C1/p11')
        payload=notion._req.call_args.args[2]
        notes=a.value({'properties':payload['properties']},'Next Step Notes')
        self.assertIn('[request:request-1:Pending]',notes)
        self.assertIn('partners@runpod.io',notes);self.assertIn('Never send',notes)
        self.assertEqual(log.call_args.args[2],'Card decision')
    def test_guide_failure_keeps_reviewed_routes_and_logs_drift(self):
        before=copy.deepcopy(a.ROUTES)
        with patch.object(a,'_GUIDE_CHECKED',0),patch.object(a,'audit') as log:
            a.refresh_guide(Mock(side_effect=TimeoutError('read failed')))
        self.assertEqual(a.ROUTES,before)
        self.assertIn('DRIFT:',log.call_args.args[3])
    def test_queue_producer_reuses_existing_failed_row_without_send(self):
        p=self.setup();self.remote.patch(p['id'],{'Status':{'select':{'name':'Failed'}}})
        notion=Mock()
        with patch.object(a,'capture',return_value=self.remote.get(self.iid)),patch.object(a,'set_route',return_value='Supply Team'),patch.object(a,'query',return_value=iter([self.remote.get(p['id'])])):
            result=a.queue_handoff(notion,{'completion_source':{},'completion_route':'Supply Team','channel':'C0CAL8C08RK','key':'x'})
        self.assertEqual(result['id'],p['id']);notion._req.assert_not_called()
    def test_create_failure_prevents_review_model_and_handoff(self):
        import review
        from intake import Store
        store=Store(self.tmp.name+'/jobs');event={'key':'x','kind':'review','channel':'C123','ts':'100.1','thread_ts':'100.1','event_id':'e'}
        store.enqueue(event);client=Mock();ledger=review.Ledger(store.path)
        with patch('intake_alignment.capture',side_effect=TimeoutError()):
            review.process(store,store.get('x'),client,ledger,{'notion_write':True,'routing_enabled':True})
        client.review.assert_not_called()
    def test_all_sources_recap_reports_pending_pickup_and_draft_not_sent(self):
        self.setup()
        p=self.remote.pages[self.iid];p['properties'].update({'Company':a.rich('Email lead'),'Source':{'select':{'name':'Gmail'}},'Gmail Draft ID':a.rich('draft1')})
        class Reader:
            def notion(inner,method,path,data):
                return {'results':[p] if a.INTAKE in path else []}
        with patch.object(a,'refresh_guide'):
            text=a.build_recap(Reader(),dt.datetime.now(dt.timezone.utc),0)
        self.assertIn('Gmail',text);self.assertIn('Drafts awaiting your send',text);self.assertIn('not an email sent',text)


    def test_recap_bounds_large_backlog_and_preserves_counts(self):
        pages=[]
        for n in range(800):
            p=prop_page(str(uuid.UUID(int=n+2000)), **{'Company':'Lead '+str(n), 'Status':'Flagged', 'Source':'Email Direct', 'Request Summary':'A request '*30})
            p['last_edited_time']='2026-11-05T12:00:00Z'
            pages.append(p)
        class Reader:
            def notion(inner,method,path,data):
                return {'results':pages if a.INTAKE in path else []}
        with patch.object(a,'refresh_guide'):
            text=a.build_recap(Reader(),dt.datetime.now(dt.timezone.utc),0)
        self.assertLess(len(text),9000)
        self.assertIn('Your decisions (800)',text)
        self.assertIn('additional records',text)
        self.assertIn('Email Direct: 800',text)
        self.assertIn('*Guide:*',text)

    def test_inactive_or_missing_owner_is_held_and_logged(self):
        p=self.setup();original=self.remote.slack
        def slack(method,data):
            if method=='users.info': return {'user':{'id':data['user'],'deleted':True}}
            return original(method,data)
        self.remote.slack=slack
        self.box.post(p)
        self.assertFalse(self.posts())
        self.assertEqual(a.value(self.remote.get(p['id']),'Status'),'Failed')
        self.assertTrue(any(a.value(p,'Detail').startswith('DRIFT:') for p in self.remote.activity))

    def test_guide_line_reports_durable_drift_and_degraded_cache(self):
        self.setup()
        a.audit(self.remote.notion,'drift1','Reconciliation','DRIFT: section 5 - owner absent')
        with patch.dict(a._GUIDE_STATE,degraded=True,reason='Guide unavailable',reviewed='2020-01-01'):
            line=a.guide_summary(self.remote.notion,0)
        self.assertIn('1 drift events',line)
        self.assertIn('review overdue',line)
        self.assertIn('last reviewed snapshot',line)

    def test_alias_source_receipt_gets_check(self):
        p=self.setup()
        self.remote.patch(self.iid,{'Intake Ref':a.rich('slack:C0BQKEQLSKH:100.1')})
        self.box.post(p)
        self.assertTrue(any(m=='reactions.add' and d['timestamp']=='100.1' for m,d in self.remote.calls))


    def test_guide_owner_version_change_refreshes_but_policy_change_blocks(self):
        def rich(text): return [{'plain_text':text}]
        root=[{'type':'paragraph','paragraph':{'rich_text':rich('Guide version: 2026-11-05.2 Last reviewed: 2026-11-05')}},
              {'type':'heading_2','heading_2':{'rich_text':rich('1. Core rules')}},
              {'type':'paragraph','paragraph':{'rich_text':rich('Never send email')}},
              {'type':'heading_2','heading_2':{'rich_text':rich('5. Routing rules')}},
              {'id':'routes','type':'table'}]
        version_block=root[0]
        root[0]={'id':'metadata-callout','type':'callout','callout':{'rich_text':rich('Source of truth')},'has_children':True}
        scenarios=['40fff732fc348187b3cbf23755ad4ea5','40fff732fc3481e2894cc6bbfa2f97dc']
        owners={k:v for k,v in a.ROUTES.items()}
        def request(method,path,data=None):
            if '/40fff732-fc34-810e-ac53-f085a6d47576/' in path: return {'results':root}
            if '/40fff732-fc34-80f9-b488-cfafd7a7b257/' in path: return {'results':[{'id':'library','type':'callout','has_children':True}]}
            if '/library/' in path: return {'results':[{'id':id,'type':'child_page'} for id in scenarios]}
            if any('/'+id+'/' in path for id in scenarios): return {'results':[]}
            if '/metadata-callout/' in path: return {'results':[version_block]}
            if '/routes/' in path:
                return {'results':[{'type':'table_row','table_row':{'cells':[rich(k),rich(''),rich(c),rich(' '.join(us))]}} for k,(c,us) in owners.items()]}
            raise AssertionError(path)
        with patch.object(a,'_GUIDE_CHECKED',0),patch.object(a,'_GUIDE_CACHE',None),patch.object(a,'GUIDE_VERSION','2026-11-05.2'),patch.dict(a.ROUTES),patch.dict(a._GUIDE_STATE),patch.object(a,'audit'):
            a.refresh_guide(request)
            self.assertFalse(a._GUIDE_STATE['degraded'])
            version_block['paragraph']['rich_text']=rich('Guide version: 2026-11-05.3 Last reviewed: 2026-11-05')
            owners['Affiliate Program']=('C0CD3M1QG4U',('U0123456789',))
            a._GUIDE_CHECKED=0;a.refresh_guide(request)
            self.assertEqual(a.GUIDE_VERSION,'2026-11-05.3')
            self.assertEqual(a.ROUTES['Affiliate Program'][1],('U0123456789',))
            version_block['paragraph']['rich_text']=rich('Guide version: 2026-11-05.4 Last reviewed: 2026-11-05')
            root[2]['paragraph']['rich_text']=rich('Send email automatically')
            a._GUIDE_CHECKED=0;a.refresh_guide(request)
            self.assertTrue(a._GUIDE_STATE['blocked'])
            self.assertEqual(a.GUIDE_VERSION,'2026-11-05.3')
            root[2]['paragraph']['rich_text']=rich('Never send email')
            version_block['paragraph']['rich_text']=rich('Guide version: 2026-11-05.3 Last reviewed: 2026-11-05')
            scenarios.append('unreviewed-scenario')
            a._GUIDE_CHECKED=0;a.refresh_guide(request)
            self.assertTrue(a._GUIDE_STATE['blocked'])
            self.assertIn('New scenario pages',a._GUIDE_STATE['reason'])

    def test_zoe_feedback_requires_mention_and_thread(self):
        import weekly_feedback as w
        body={'event_id':'Ev1','event':{'type':'app_mention','user':'U0CE06U7K52','channel':outbox.WEEKLY_CHANNEL,'ts':'10.2','thread_ts':'10.1','text':'<@'+outbox.BOT+'> Research update'}}
        with patch.dict(os.environ,WEEKLY_FEEDBACK_ENABLED='true'):
            self.assertIsNotNone(w.accept(body))
            body['event'].pop('thread_ts')
            self.assertIsNone(w.accept(body))

    def test_channel_routes_have_no_automatic_email_draft(self):
        import review
        for route in a.ROUTES:
            with self.subTest(route=route):
                self.assertEqual(review.drafts({'route':route},'contact@example.com','2026-11-05'),[])

if __name__=='__main__':unittest.main()
