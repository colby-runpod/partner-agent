import datetime as dt
import json
import unittest
from unittest.mock import patch, Mock
import automation_ledger as l
import intake_alignment as a

class Store:
    def __init__(self): self.data={}
    def get(self,k,default=None): return self.data.get(k,default)
    def put(self,k,v): self.data[k]=v

def page(**props):
    return {'id':'p1','properties':{k:({'checkbox':v} if isinstance(v,bool) else a.rich(v)) for k,v in props.items()}}

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();l.bind(self.store)
        self.patch=patch.object(l,'_snapshot',{});self.patch.start();self.addCleanup(self.patch.stop)
        self.addCleanup(l.bind,None)
        self.now=dt.datetime(2026,11,11,1,tzinfo=dt.timezone.utc)
    def test_mountain_run_day_matches_audit(self):
        self.assertEqual(l.run_name(self.now),'tpa 2026-11-10')
        calls=[]
        def req(m,p,d=None):
            calls.append(d)
            return {'results':[]} if p.endswith('/query') else {'id':'event'}
        a.audit(req,'key','Note','detail',when=self.now.isoformat())
        self.assertEqual(a.value({'properties':calls[-1]['properties']},'Run'),'tpa 2026-11-10')
    def test_read_three_latest_rows_sorted_and_no_actions(self):
        rows=[page(**{'Guide Version':'2026-11-10.1','Agent Updated':False}),page(**{'Guide Version':'2026-11-09.1'}),page(**{'Open Items':'Team Partner Agent: S3 handoff pending'})]
        req=Mock(side_effect=[{'results':[r]} for r in rows])
        snap=l.read(req)
        self.assertTrue(snap['changed']);self.assertIn('S3',snap['open_items'])
        self.assertEqual(req.call_count,3)
        for call in req.call_args_list:
            self.assertEqual(call.args[2]['sorts'],[{'property':'Entry ID','direction':'descending'}])
    def test_outage_note_once_across_restart_until_write_recovers(self):
        req=Mock(side_effect=TimeoutError())
        with patch.object(a,'audit') as audit:
            l.read(req);l.read(req);l.bind(self.store);l.read(req)
            self.assertEqual(audit.call_count,1)
            l.recovered();l.read(req)
            self.assertEqual(audit.call_count,2)
    def test_successful_recap_idempotent_and_versioned(self):
        calls=[]
        def req(m,p,d=None):
            calls.append((m,p,d))
            return {'results':[]} if p.endswith('/query') else {'id':'run1'}
        l._snapshot.update(guide=page(**{'Guide Version':a.GUIDE_VERSION,'Agent Updated':False}),changed=True)
        result=l.finish(req,self.store,self.now,self.now.isoformat(),'Complete','Posted',['1.2'])
        self.assertEqual(result,'run1')
        l.finish(req,self.store,self.now,self.now.isoformat(),'Complete','Posted',['1.2'])
        creates=[c for c in calls if c[1]=='pages'];self.assertEqual(len(creates),1)
        props=creates[0][2]['properties']
        self.assertEqual(a.value({'properties':props},'Guide Version'),a.GUIDE_VERSION)
        self.assertIn('not yet updated',a.value({'properties':props},'Result'))
    def test_ambiguous_create_does_not_recreate(self):
        creates=[]
        def req(m,p,d=None):
            if p.endswith('/query'):return {'results':[]}
            creates.append(d);raise TimeoutError()
        with patch.object(a,'audit'):
            self.assertIsNone(l.finish(req,self.store,self.now,self.now.isoformat(),'Complete','Posted',['1.2']))
            self.assertIsNone(l.finish(req,self.store,self.now,self.now.isoformat(),'Complete','Posted',['1.2']))
        self.assertEqual(len(creates),1)
    def test_ambiguous_create_adopts_found_receipt(self):
        self.store.put('automation-ledger-run:tpa 2026-11-10',json.dumps({'attempted':True}))
        req=Mock(return_value={'results':[{'id':'existing'}]})
        self.assertEqual(l.finish(req,self.store,self.now,self.now.isoformat(),'Complete','Posted'),'existing')
        self.assertEqual(req.call_count,1)
    def test_read_or_log_failure_never_raises(self):
        with patch.object(a,'audit',side_effect=RuntimeError()):
            self.assertIn('fallback',l.read(Mock(side_effect=RuntimeError())))
    def test_agent_missing_runs_not_drift_before_enabled(self):
        l._snapshot.update(guide=page(**{'Agent Updated':False}))
        result=l.gap_report(Mock(return_value={'results':[]}),self.now)
        self.assertNotIn('Team Partner Agent',result)
        self.assertIn('Pipeline',result)

if __name__=='__main__':unittest.main()
