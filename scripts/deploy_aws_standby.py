"""Deploy the known health-only entrypoint; never inherit business credentials."""
import json
import os
import subprocess
import time
import urllib.request


def aws(*args):
    return json.loads(subprocess.check_output(['aws', *args, '--output', 'json']))


def main():
    service = os.environ['SERVICE_ARN']
    container = {
        'image': os.environ['DEPLOY_IMAGE'],
        'containerPort': 8080,
        'command': ['python', 'service.py'],
        'environment': [{'name': 'AGENT_RUNTIME_MODE', 'value': 'standby'}],
        'secrets': [],
    }
    # No credentials or previous runtime environment are copied into the request.
    aws('ecs', 'update-express-gateway-service', '--service-arn', service,
        '--primary-container', json.dumps(container), '--health-check-path', '/healthz',
        '--scaling-target', json.dumps({'minTaskCount': 1, 'maxTaskCount': 1}),
        '--cpu-architecture', 'X86_64')
    for _ in range(90):
        state = aws('ecs', 'describe-services', '--cluster', 'default', '--services', service)['services'][0]
        primary = next(d for d in state['deployments'] if d['status'] == 'PRIMARY')
        if primary.get('rolloutState') == 'FAILED':
            raise RuntimeError('Deployment failed; inspect ECS deployment events.')
        if primary.get('rolloutState') == 'COMPLETED' and state['runningCount'] == 1:
            task = aws('ecs', 'describe-task-definition', '--task-definition', primary['taskDefinition'])['taskDefinition']
            containers = task['containerDefinitions']
            if len(containers) != 1 or containers[0]['image'] != container['image']:
                # ECS may briefly describe the previous deployment after update.
                time.sleep(10)
                continue
            actual = containers[0]
            if actual.get('secrets') or actual.get('environment') != container['environment'] or actual.get('command') != container['command']:
                raise RuntimeError('Standby configuration mismatch; do not promote this deployment.')
            with urllib.request.urlopen(os.environ['HEALTH_URL'], timeout=15) as response:
                health = json.load(response)
            if health != {'status': 'standby', 'processing_enabled': False}:
                raise RuntimeError('Standby health verification failed.')
            print('Verified requested image running in standby with processing disabled.')
            return
        time.sleep(10)
    raise TimeoutError('Deployment did not complete in 15 minutes; inspect ECS before retrying.')


if __name__ == '__main__':
    main()
