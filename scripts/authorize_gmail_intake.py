"""Authorize read-only Gmail locally and store the credential directly in AWS.

Requires google-auth-oauthlib in a local virtualenv and an authenticated AWS CLI.
Never prints tokens. Do not add the downloaded Google client JSON to this repo.
"""
import argparse
import json
import os
import subprocess
import tempfile
import urllib.request


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client_json',help='Downloaded Google Desktop OAuth client JSON outside this repository')
    args=parser.parse_args()
    identity=subprocess.run(['aws','sts','get-caller-identity','--region','us-east-2','--output','json'],capture_output=True,text=True,check=True)
    if json.loads(identity.stdout)['Account']!='626963115342': raise SystemExit('Wrong AWS account')
    flow=InstalledAppFlow.from_client_secrets_file(args.client_json,['https://www.googleapis.com/auth/gmail.readonly'])
    credential=flow.run_local_server(host='localhost',port=0,access_type='offline',prompt='consent',open_browser=True)
    if not credential.refresh_token: raise SystemExit('No refresh token returned; authorization not stored')
    req=urllib.request.Request('https://gmail.googleapis.com/gmail/v1/users/me/profile',headers={'Authorization':'Bearer '+credential.token})
    with urllib.request.urlopen(req,timeout=30) as response: profile=json.load(response)
    if profile.get('emailAddress','').lower()!='colby.henderson@runpod.io': raise SystemExit('Wrong Gmail account; authorization not stored')
    secret='team-partner-agent/pilot/gmail-oauth'
    result=subprocess.run(['aws','secretsmanager','describe-secret','--region','us-east-2','--secret-id',secret],capture_output=True,text=True)
    if result.returncode and 'ResourceNotFoundException' not in result.stderr: raise SystemExit('Cannot inspect AWS secret')
    payload={'client_id':credential.client_id,'client_secret':credential.client_secret,'refresh_token':credential.refresh_token}
    name=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',prefix='partner-gmail-',suffix='.json',delete=False) as f:
            name=f.name;os.chmod(name,0o600);json.dump(payload,f)
        action=['put-secret-value','--secret-id',secret] if result.returncode==0 else ['create-secret','--name',secret]
        saved=subprocess.run(['aws','secretsmanager',*action,'--region','us-east-2','--secret-string','file://'+name],capture_output=True,text=True)
        if saved.returncode: raise SystemExit('AWS could not store the credential; no secret value was printed')
    finally:
        if name: os.unlink(name)
    print('Read-only Gmail authorization saved to '+secret+'. Intake is not activated by this script.')

if __name__=='__main__': main()
