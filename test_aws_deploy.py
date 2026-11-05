import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import deploy_aws_standby as deploy


class DeploymentTests(unittest.TestCase):
    def test_active_runtime_modules_are_copied_into_image(self):
        dockerfile = Path(__file__).with_name('Dockerfile').read_text()
        self.assertIn('review.py working_task_brief.py tasks.py', dockerfile)
        self.assertIn('p_ops_runtime.py p_ops_service.py', dockerfile)

    def test_working_task_brief_is_owned_only_by_partner_ops(self):
        review = Path(__file__).with_name('review.py').read_text()
        p_ops = Path(__file__).with_name('p_ops_runtime.py').read_text()
        self.assertNotIn('working_task_brief.run', review)
        self.assertIn('working_task_brief.run', p_ops)
        self.assertIn('PARTNER_OPS_APP_ID = "A0CA2C43D4L"', p_ops)

    def exercise(self, health, secrets=None):
        image = 'example@sha256:abc'
        task = {'containerDefinitions': [{
            'image': image, 'command': ['python', 'service.py'],
            'environment': [{'name': 'AGENT_RUNTIME_MODE', 'value': 'standby'}],
            'secrets': secrets or [],
        }]}
        state = {'services': [{'runningCount': 1, 'deployments': [{
            'status': 'PRIMARY', 'rolloutState': 'COMPLETED', 'taskDefinition': 'test',
        }]}]}
        with patch.dict('os.environ', {'SERVICE_ARN': 'test', 'DEPLOY_IMAGE': image, 'HEALTH_URL': 'https://example/healthz'}), \
             patch.object(deploy, 'aws', side_effect=[{}, state, {'taskDefinition': task}]) as api, \
             patch.object(deploy.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(health).encode())):
            deploy.main()
            args = api.call_args_list[0].args
            request = json.loads(args[args.index('--primary-container') + 1])
            self.assertEqual(request['secrets'], [])
            self.assertEqual(request['environment'], [{'name': 'AGENT_RUNTIME_MODE', 'value': 'standby'}])

    def test_verified_standby(self):
        self.exercise({'status': 'standby', 'processing_enabled': False})

    def test_active_health_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'health verification'):
            self.exercise({'status': 'active', 'processing_enabled': True})

    def test_inherited_secret_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'configuration mismatch'):
            self.exercise({}, [{'name': 'NOTION_TOKEN', 'valueFrom': 'test'}])
