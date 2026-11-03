import json,tempfile,unittest
import review
from test_review import GOOD
class RequestClarity(unittest.TestCase):
 def test_fields_survive_parse_render_and_saved_review(self):
  d=dict(GOOD,requested_partnership='Inferred: Referral partnership',requested_action='Wants to introduce two customers.',offered_value='Claims two customer opportunities.')
  r=review.parse(json.dumps(d));text=review.render(r)
  self.assertLess(text.index('*Their request:*'),text.index('*Suggested next step:*'))
  self.assertIn('Inferred: Referral partnership',text)
  with tempfile.TemporaryDirectory() as p:
   l=review.Ledger(p+'/db');l.record('k',dict(channel='C',ts='1.1',thread_ts='1.1'),r,'ok')
   data=json.loads(l.review('k')['data'])
   for k in ('requested_partnership','requested_action','offered_value'):self.assertEqual(data[k],r[k])
 def test_legacy_review_does_not_infer_request_from_route(self):
  r=review.parse(json.dumps(GOOD));r['route']='Supply Team'
  self.assertIn('Unspecified partnership inquiry',review.render(r))
  self.assertIn('No specific request captured',review.render(r))
