"""Prepare, but never apply, a fresh-start ECS container configuration."""
import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

REGION='us-east-2'
SERVICE='team-partner-agent-a223'

def aws(*args):
    result=subprocess.run(['aws',*args,'--region',REGION,'--output','json'],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--review-channel',required=True)
    parser.add_argument('--cutoff',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if not args.review_channel.startswith('C') or not args.review_channel.isalnum():raise ValueError('Invalid channel ID')
    boundary=dt.datetime.fromisoformat(args.cutoff.replace('Z','+00:00'))
    if boundary.tzinfo is None:raise ValueError('Cutoff must include timezone')
    # This script does not establish legacy shutdown; the operator verifies it.
    service=aws('ecs','describe-services','--cluster','default','--services',SERVICE)['services'][0]
    if len(service['deployments'])!=1 or service['deployments'][0]['rolloutState']!='COMPLETED':raise ValueError('Existing deployment not stable')
    task=aws('ecs','describe-task-definition','--task-definition',service['deployments'][0]['taskDefinition'])['taskDefinition']
    container=task['containerDefinitions'][0]
    if container.get('environment')!=[{'name':'AGENT_RUNTIME_MODE','value':'standby'}]:raise ValueError('Expected standby source')
    image=container['image']
    if '@sha256:' not in image:raise ValueError('Pin immutable image')
    pairs=[('SLACK_BOT_TOKEN','slack-bot-token'),('SLACK_SIGNING_SECRET','slack-signing-secret'),('NOTION_TOKEN','notion-token'),('ANTHROPIC_API_KEY','anthropic-api-key'),('RUNTIME_CREDENTIAL','database-runtime')]
    refs=[]
    for name,suffix in pairs:
        secret=aws('secretsmanager','describe-secret','--secret-id','team-partner-agent/pilot/'+suffix)
        if not any('AWSCURRENT' in stages for stages in secret.get('VersionIdsToStages',{}).values()):raise ValueError('Secret not populated')
        refs.append({'name':name,'valueFrom':secret['ARN']})
    env=dict(AGENT_RUNTIME_MODE='active',STORAGE_BACKEND='postgres',POSTGRES_FRESH_START_AT=args.cutoff,
             OUTBOX_CREATED_AFTER=args.cutoff,LEGACY_WRITERS_STOPPED='true',
             DB_HOST='team-partner-agent-pilot.cl48coiwscwn.us-east-2.rds.amazonaws.com',
             SLACK_TEAM_ID='T06F03V8NUQ',SLACK_APP_ID='A0C76FQTM62',ANTHROPIC_MODEL='claude-sonnet-4-6',
             REVIEW_ENABLED='true',REVIEW_MODE='shadow',REVIEW_CHANNEL_IDS='C0BQKEQLSKH',
             REVIEW_SHADOW_CHANNEL_ID=args.review_channel,SUMMARY_ENABLED='false',CHAT_ENABLED='false',
             TASKS_ENABLED='false',AGENT_ENABLED='false',INTAKE_ENABLED='false',NOTION_WRITE='true',
             INTERACTIVE_ENABLED='true',OUTBOX_ENABLED='true',SCREENING_V2_ENABLED='true',
             OUTBOX_TEST_IDS='402ff732-fc34-81d8-85e3-f6c01f1b2fdd')
    result={'image':image,'containerPort':8080,'command':['python','service.py'],
            'environment':[{'name':k,'value':v} for k,v in env.items()],'secrets':refs}
    with Path(args.output).open('x') as file:json.dump(result,file,indent=2)
    print('Prepared configuration only; no service, IAM policy or secret values changed.')

if __name__=='__main__':main()
