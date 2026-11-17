"""Manual main-branch active release with OIDC; preserve configuration and send fences."""
import json,os,subprocess,time,urllib.request
REGION='us-east-2'
SERVICE='arn:aws:ecs:us-east-2:626963115342:service/default/team-partner-agent-a223'

def aws(*args):
    return json.loads(subprocess.check_output(['aws',*args,'--region',REGION,'--output','json']))

def container_for(config,image):
    if not image.startswith('626963115342.dkr.ecr.us-east-2.amazonaws.com/team-partner-agent@sha256:'):
        raise ValueError('Use the immutable approved repository digest')
    c=dict(config['primaryContainer'])
    if not c.get('secrets') or not c.get('command') or 'worker_ownership' not in ' '.join(c['command']):
        raise ValueError('Active service must retain credentials and receiver-only ownership guard')
    c['image']=image
    return c

RELEASE='''import hashlib,storage,outbox,intake_alignment as a
s=storage.make_store(None);a.refresh_guide(outbox.Remote().notion)
assert not a._GUIDE_STATE.get('blocked') and not a._GUIDE_STATE.get('degraded')
with s.connect() as db:
 for role in ('queue','outbox'):
  lock=int.from_bytes(hashlib.sha256((s.namespace+':'+role).encode()).digest()[:8],'big',signed=True)
  assert db.execute('SELECT pg_try_advisory_xact_lock(%s) AS ok',(lock,)).fetchone()['ok']
 assert not db.execute("SELECT id FROM partner_agent.outbox_delivery WHERE namespace=%s AND state IN ('sending','sync','deciding')",(s.namespace,)).fetchall()
 assert not db.execute("SELECT key FROM partner_agent.jobs WHERE namespace=%s AND (state='sending' OR reply_state IN ('sending','review'))",(s.namespace,)).fetchall()
 db.execute('UPDATE partner_agent.worker_ownership SET active=FALSE WHERE namespace=%s AND active=TRUE',(s.namespace,))
print('Receipt guards passed; worker holds released')
'''

def main():
    image=os.environ['DEPLOY_IMAGE']
    config=aws('ecs','describe-express-gateway-service','--service-arn',SERVICE)['service']['activeConfigurations'][0]
    old=aws('ecs','list-tasks','--cluster','default','--service-name','team-partner-agent-a223')['taskArns']
    c=container_for(config,image)
    aws('ecs','update-express-gateway-service','--service-arn',SERVICE,'--primary-container',json.dumps(c))
    definition=None
    for _ in range(150):
        svc=aws('ecs','describe-services','--cluster','default','--services',SERVICE)['services'][0]
        deployments=svc['deployments'];p=next(d for d in deployments if d['status']=='PRIMARY')
        if p.get('rolloutState')=='FAILED':raise RuntimeError('Deployment failed; holds remain')
        td=aws('ecs','describe-task-definition','--task-definition',p['taskDefinition'])['taskDefinition']
        old_stopped=not old or all(t['lastStatus']=='STOPPED' for t in aws('ecs','describe-tasks','--cluster','default','--tasks',*old)['tasks'])
        if len(deployments)==1 and p.get('rolloutState')=='COMPLETED' and td['containerDefinitions'][0]['image']==image and old_stopped:
            definition=td;break
        time.sleep(10)
    if definition is None:raise TimeoutError('Rollout not verified; worker holds retained')
    net={'awsvpcConfiguration':dict(config['networkConfiguration'],assignPublicIp='ENABLED')}
    override={'containerOverrides':[{'name':definition['containerDefinitions'][0]['name'],'command':['python','-c',RELEASE]}]}
    task=aws('ecs','run-task','--cluster','default','--task-definition',definition['taskDefinitionArn'],'--launch-type','FARGATE','--network-configuration',json.dumps(net),'--overrides',json.dumps(override))
    if task.get('failures') or not task.get('tasks'):raise RuntimeError('Reconciliation task could not start; holds retained')
    arn=task['tasks'][0]['taskArn']
    for _ in range(60):
        t=aws('ecs','describe-tasks','--cluster','default','--tasks',arn)['tasks'][0]
        if t['lastStatus']=='STOPPED':
            if any(c.get('exitCode')!=0 for c in t['containers']):raise RuntimeError('Reconciliation failed; inspect task before retry')
            break
        time.sleep(5)
    else:raise TimeoutError('Reconciliation incomplete; do not repeat blindly')
    with urllib.request.urlopen('https://te-9776df4994d4439c9de9b95961285a76.ecs.us-east-2.on.aws/healthz',timeout=15) as r:
        assert r.status==200
    print(json.dumps({'deployed_task_definition':definition['taskDefinitionArn'],'image':image,'receipt_guards':'passed','health':200,'note':'Verify new lead delivery separately; server health is not delivery evidence.'}))
if __name__=='__main__':main()
