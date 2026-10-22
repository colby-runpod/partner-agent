import io
import json
import unittest
from unittest.mock import patch
from scripts import deploy_aws_standby as deploy


class DeploymentTests(unittest.TestCase):
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
