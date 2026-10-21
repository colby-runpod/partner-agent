import copy
import json
import unittest
import screening
import review
from test_review import GOOD


def facts(a,s,i,c,v,**kw):
    return dict(scores=dict(zip('ASICV',(a,s,i,c,v))),own_domain=True,**kw)


class ScreeningTests(unittest.TestCase):
    def test_calibration(self):
        cases=[('Kovara return',facts(3,1,2,3,2),'Act now',26),
               ('Kovara original',facts(2,1,1,0,2),'Qualify',13),
               ('Eranyacloud',facts(2,3,1,1,2),'Qualify',20),
               ('ACKstorm blank',facts(0,3,3,0,3),'Qualify',15),
               ('ACKstorm source',facts(2,3,3,0,3),'Act now',21),
               ('PYXON',facts(1,2,1,1,1),'Qualify',13),
               ('Corix',facts(1,2,1,0,2),'Qualify',12),
               ('Accruex',facts(1,1,1,1,1,teammate_referred=True),'Qualify',11),
               ('generic',facts(1,1,1,0,1),'DQ',8)]
        for name,f,tier,total in cases:
            with self.subTest(name=name):
                r=screening.evaluate(f);self.assertEqual((r['tier'],r['total']),(tier,total))
    def test_gates_precede_scores(self):
        for risk in ('spam','misrepresentation','policy_review'):
            self.assertEqual(screening.evaluate(facts(3,2,3,3,3,risk=risk,risk_evidence='documented finding'))['tier'],'DQ')
        self.assertEqual(screening.evaluate(facts(3,3,3,3,3,risk='policy_review',risk_evidence='needs review'))['tier'],'Hold for Colby')
        self.assertEqual(screening.evaluate(facts(3,3,3,3,3,wrong_door='sales'))['tier'],'Route')
        self.assertEqual(screening.evaluate(facts(3,2,3,3,3,predevelopment_supply=True))['tier'],'DQ')
    def test_missing_access_not_evidence_for_dq(self):
        self.assertEqual(screening.evaluate(facts(0,0,0,0,0,blank_payload=True))['tier'],'Needs research')
        f=facts(1,1,1,0,1,unverifiable=True)
        self.assertEqual(screening.evaluate(f)['tier'],'Needs research')
        f['research']={k:'Searched, no corroboration' for k in screening.RESEARCH}
        self.assertEqual(screening.evaluate(f)['tier'],'DQ')
    def test_specific_software_floor_requires_domain(self):
        f=facts(2,1,1,0,2,unverifiable=True)
        self.assertEqual(screening.evaluate(f)['tier'],'Qualify')
        f['own_domain']=False
        self.assertEqual(screening.evaluate(f)['tier'],'Needs research')
    def test_strict_scores(self):
        for val in (True,4,-1,'2',1.5):
            f=facts(1,1,1,1,1);f['scores']['A']=val
            with self.assertRaises(ValueError):screening.evaluate(f)
    def test_dq_select_reserved_for_spam(self):
        self.assertEqual(screening.apply({}, facts(1,1,1,0,1))['score'], '2')
        self.assertEqual(screening.apply({}, facts(1,1,1,0,1,risk='spam',risk_evidence='spam pitch'))['score'], 'DQ')
    def test_legacy_unchanged_v2_opt_in_and_ledger_notes(self):
        d=copy.deepcopy(GOOD);d['screening']=facts(1,1,1,0,1,unverifiable=True,research={k:'done' for k in screening.RESEARCH})
        self.assertEqual(review.parse(json.dumps(d))['score'],'4')
        r=review.parse(json.dumps(d),screening_v2=True)
        self.assertEqual(r['screening']['tier'],'Needs research')
        self.assertIn('8/33',review.render(r))
        props=review.NotionWriter({}).properties(r,'test')
        notes=''.join(x['text']['content'] for x in props['Quality Notes']['rich_text'])
        self.assertIn('8/33',notes)
        self.assertNotEqual(props['Qualification Score']['select']['name'],'8')

if __name__=='__main__':unittest.main()
