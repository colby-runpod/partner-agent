import json,unittest,datetime as dt
from unittest.mock import patch
import operations_health as h
import intake_alignment as a
from test_automation_ledger import Store
from test_outbox import page
class StabilizationHealth(unittest.TestCase):
 def test_listener_cannot_mask_stale_or_stalled_workers(self):
  s=Store();h.pulse(s,'queue',job={'key':'one'},now=1000);h.pulse(s,'outbox',now=1000)
  self.assertTrue(h.readiness(s,now=1001)['ready'])
  self.assertFalse(h.readiness(s,now=1700)['ready'])
  h.pulse(s,'queue',job={'key':'one'},now=2000);h.pulse(s,'outbox',now=2000)
  self.assertIn('queue_job_stalled',h.readiness(s,now=2001)['reasons'])
 def test_failure_survives_heartbeat_until_confirmed_success(self):
  s=Store();h.pulse(s,'queue',error='failed',now=1000);h.pulse(s,'queue',now=1040)
  self.assertFalse(h.readiness(s,roles=('queue',),now=1041)['ready'])
  h.pulse(s,'queue',error='',now=1042)
  self.assertTrue(h.readiness(s,roles=('queue',),now=1043)['ready'])
 def test_blocked_and_scheduled_not_ready_backlog(self):
  ready=page(1,Title='pipeline:one');ready['created_time']='2026-11-15T00:00:00Z'
  held=page(2,Title='pipeline:two',Error='Held by routing policy');held['created_time']=ready['created_time']
  parent=page(3,Title='Invitation:three',**{'Thread TS':'parent:OUT-1'});parent['created_time']=ready['created_time']
  test=page(12,Title='TEST');test['created_time']=ready['created_time']
  for item in (ready,held,parent,test):
   title=item['properties']['Title'];title['title']=title.pop('rich_text');title['type']='title'
  result=h.classify_backlog([ready,held,parent,test],dt.datetime(2026,11,17,tzinfo=dt.timezone.utc))
  self.assertEqual(result,dict(ready=1,ready_overdue=1,blocked=2,scheduled=0,excluded=1))
 def test_unrelated_working_task_edits_do_not_change_policy(self):
  def block(kind,text):return {'type':kind,kind:{'rich_text':[{'plain_text':text}]}}
  root=[block('heading_2','1. Core rules'),block('paragraph','one'),block('heading_2','8b. Working Task suite'),block('paragraph','old'),block('heading_2','9. Hard limits'),block('paragraph','never send')]
  first=a.policy_fingerprint(root,lambda _:[]);root[3]=block('paragraph','new working task procedure')
  self.assertEqual(first,a.policy_fingerprint(root,lambda _:[]))
  root[5]=block('paragraph','send now');self.assertNotEqual(first,a.policy_fingerprint(root,lambda _:[]))
 def test_durable_cache_rejects_unknown_destinations(self):
  s=Store();routes={k:list(v) for k,v in a.ROUTES.items()}
  saved=dict(fingerprint_scope='intake-v2',policy='a'*64,routes=routes)
  with patch.object(a,'_GUIDE_CACHE',None),patch.dict(a._GUIDE_STATE,{},clear=True),patch.dict(a.ROUTES,a.ROUTES.copy(),clear=True):
   s.put('guide:approved:'+a.GUIDE_VERSION,json.dumps(saved));a.restore_guide(s)
   self.assertIsNotNone(a._GUIDE_CACHE);self.assertFalse(a._GUIDE_STATE['blocked'])
  routes['Supply Team'][0]='CUNAPPROVED'
  with patch.object(a,'_GUIDE_CACHE',None):
   s.put('guide:approved:'+a.GUIDE_VERSION,json.dumps(saved));a.restore_guide(s);self.assertIsNone(a._GUIDE_CACHE)

class TemplateSemantics(unittest.TestCase):
 def test_screening_is_distinct_from_compatibility_bucket(self):
  from partner_sources import qualification_context
  self.assertIn('22/33',qualification_context({'score':'4','screening':{'total':22,'tier':'Act now'}}))
  self.assertIn('compatibility bucket',qualification_context({'score':'4','screening':{'total':22,'tier':'Act now'}}))
  self.assertIn('not available',qualification_context({'score':'2'}))
