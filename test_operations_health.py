import unittest,datetime as dt
from unittest.mock import Mock
from operations_health import notify_once,outbox_health
from test_automation_ledger import Store
from test_outbox import page
class Health(unittest.TestCase):
 def test_alert_fenced_on_success_and_timeout(self):
  for fail in (False,True):
   s=Store();send=Mock(side_effect=TimeoutError() if fail else None)
   try:notify_once(s,send,'incident','message')
   except TimeoutError:pass
   notify_once(s,send,'incident','message');self.assertEqual(send.call_count,1)
 def test_test_rows_excluded_and_old_leads_counted(self):
  p=page(1,Title='pipeline:lead');p['created_time']='2026-11-15T00:00:00Z'
  t=page(2,Title='TEST');t['created_time']=p['created_time']
  h=outbox_health([p,t],dt.datetime(2026,11,17,tzinfo=dt.timezone.utc))
  self.assertEqual(h,dict(queued=1,overdue=1,oldest_hours=48))
