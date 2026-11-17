import unittest
from scripts.deploy_aws_active import container_for
class ActiveDeploy(unittest.TestCase):
 def test_preserves_runtime_config_and_requires_guard(self):
  c={'image':'old','command':['python','worker_ownership'],'secrets':[{'name':'TOKEN','valueFrom':'arn'}],'environment':[{'name':'SAFE','value':'true'}]}
  new=container_for({'primaryContainer':c},'626963115342.dkr.ecr.us-east-2.amazonaws.com/team-partner-agent@sha256:abc')
  self.assertEqual(new['secrets'],c['secrets']);self.assertEqual(new['environment'],c['environment']);self.assertEqual(c['image'],'old')
  with self.assertRaises(ValueError):container_for({'primaryContainer':dict(c,command=[])},new['image'])
  with self.assertRaises(ValueError):container_for({'primaryContainer':c},'other/image')
