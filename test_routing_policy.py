import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import routing_policy as policy
import review
import partner_sources as src
from test_review import GOOD

EVENT = {'key':'routing-test','channel':'C123','ts':'100.1','thread_ts':'100.1'}

class RoutingContract(unittest.TestCase):
    def parse(self, intent, subtype='unspecified', **extra):
        data=copy.deepcopy(GOOD)
        data['route']='Partnerships'
        data['routing_context']=dict(intent=intent, subtype=subtype, rationale='Explicit request', **extra)
        return review.parse(json.dumps(data))

    def test_transaction_overrides_company_category(self):
        # VC reseller, university buyer, academic hackathon and product connector.
        for intent, expected in [('resale','Partnerships'),('compute_purchase','Sales'),
            ('event_support','Events/Sponsorship'),('commercial_integration','Partnerships'),
            ('portfolio_program','VC/Incubator'),('startup_own_credits','Startup Program'),
            ('research_support','Research/University'),('media_appearance','Marketing')]:
            with self.subTest(intent=intent):
                self.assertEqual(self.parse(intent)['route'],expected)

    def test_low_quality_does_not_suppress_any_specialist(self):
        with patch.dict(os.environ, {'INTAKE_ALIGNMENT_ENABLED':'true'}):
            for intent in ('affiliate_promotion','creator_sponsorship','startup_own_credits',
                'portfolio_program','supply_capacity','event_support','research_support',
                'teaching_support','marketing_collaboration','media_appearance'):
                for score in ('1','2','DQ'):
                    r=self.parse(intent)
                    r.update(score=score,decision='do_not_pursue',screening={'tier':'DQ'})
                    r['signals'].update(below_supply_bar=True,already_in_play=True,blocker='Unverified company')
                    job=src.handoff(r,EVENT,{'routing_enabled':True})
                    self.assertIsNotNone(job,(intent,score))
                    self.assertIn('Explicit request',job['text'])
                    self.assertIn('not approval',job['text'])

    def test_holds_and_spam_still_apply(self):
        r=self.parse('supply_capacity')
        with patch.dict(os.environ, {'INTAKE_ALIGNMENT_ENABLED':'true'}):
            for change in ({'parked_by':True},{'signals':{'spam':True}},
                           {'signals':{'legal_block_citation':'https://example.com/finding'}}):
                self.assertIsNone(src.handoff(dict(r,**change),EVENT,{'routing_enabled':True}))
            self.assertIsNone(src.handoff({},EVENT,{'routing_enabled':True}))
            self.assertIsNone(src.handoff(r,dict(EVENT,synthetic_test=True),{'routing_enabled':True}))

    def test_vc_identity_does_not_override_resale(self):
        r=self.parse('resale');r['partnership_type']='VC/PE/Incubator'
        self.assertIsNone(src.handoff(r,EVENT,{'routing_enabled':True}))

    def test_secondary_consultation_not_second_post(self):
        r=self.parse('event_support','research_hackathon',secondary_consultation='Research input on workloads')
        job=src.handoff(r,EVENT,{'routing_enabled':True})
        self.assertEqual(job['channel'],'C0CAKA6PW8K')
        self.assertIn('Research input on workloads',job['text'])
        self.assertNotIn('<@U0CE06U7K52>',job['text'])

    def test_context_survives_ledger_and_renders_safely(self):
        r=self.parse('event_support','student_hackathon',event_date='2027-02-06',
                     decision_deadline='2027-02-31',secondary_consultation='<@UFAKE>')
        self.assertEqual(r['routing_context']['decision_deadline'],'')
        card=review.render(r)
        self.assertIn('2027-02-06',card)
        self.assertNotIn('<@UFAKE>',card)
        with tempfile.TemporaryDirectory() as d:
            ledger=review.Ledger(d+'/ledger.db')
            ledger.record('k',EVENT,r,'ok')
            self.assertEqual(json.loads(ledger.review('k')['data'])['routing_context'],r['routing_context'])

    def test_legacy_and_invalid_metadata_keep_saved_route(self):
        for raw in (None, [], {'intent':'invented'}, {'intent':'<@U123>'}):
            self.assertEqual(policy.normalize(raw,'Sales'),('Sales',{}))
        route,ctx=policy.normalize({'intent':'resale','subtype':'invented'},'Sales')
        self.assertEqual((route,ctx['subtype']),('Partnerships','unspecified'))

if __name__=='__main__': unittest.main()
