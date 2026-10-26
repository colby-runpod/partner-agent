"""Read-only Gmail label history -> the existing durable review queue.

First activation establishes a baseline; existing labeled mail is not replayed.
Expired history fails closed rather than silently skipping or backfilling mail.
"""
import base64
import email.utils
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)


def body_text(part):
    if part.get('filename'): return ''  # No attachment downloads or execution.
    mime=part.get('mimeType','')
    if mime in ('text/plain','text/html'):
        raw=(part.get('body') or {}).get('data','')
        if len(raw)>200000: raise ValueError('Email body exceeds intake limit')
        text=base64.urlsafe_b64decode(raw+'='*((-len(raw))%4)).decode('utf-8',errors='replace')
        if mime=='text/html':
            parser=Text(); parser.feed(text); text=' '.join(parser.parts)
        return text
    parts=part.get('parts',[])
    if mime=='multipart/alternative':
        plain=next((p for p in parts if p.get('mimeType')=='text/plain'),None)
        if plain: return body_text(plain)
    return '\n'.join(body_text(p) for p in parts)


class Gmail:
    def __init__(self): self.token=None; self.expires=0
    def get(self,path,params=None):
        if time.time()>=self.expires:
            credential=json.loads(os.environ['GMAIL_OAUTH_CREDENTIAL'])
            data=urllib.parse.urlencode({k:credential[k] for k in ('client_id','client_secret','refresh_token')}
                                        | {'grant_type':'refresh_token'}).encode()
            req=urllib.request.Request('https://oauth2.googleapis.com/token',data=data)
            try:
                with urllib.request.urlopen(req,timeout=30) as r: auth=json.load(r)
            except urllib.error.HTTPError as e: raise ValueError('Gmail OAuth HTTP '+str(e.code)) from None
            self.token=auth['access_token']; self.expires=time.time()+int(auth.get('expires_in',3600))-60
        url='https://gmail.googleapis.com/gmail/v1/users/me/'+path
        if params: url+='?'+urllib.parse.urlencode(params,doseq=True)
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.token})
        try:
            with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)
        except urllib.error.HTTPError as e: raise ValueError('Gmail API HTTP '+str(e.code)) from None


def poll(store,ledger,client,cfg):
    if not cfg.get('gmail_enabled'): return 0
    account=cfg.get('gmail_account','colby.henderson@runpod.io').lower()
    profile=client.get('profile')
    if profile.get('emailAddress','').lower()!=account: raise ValueError('Wrong Gmail account')
    label_name=cfg.get('gmail_label','Partner Inbound')
    labels=[x['id'] for x in client.get('labels').get('labels',[]) if x['name']==label_name]
    if len(labels)!=1: raise ValueError('Partner inbound label missing or ambiguous')
    marker='gmail-history:'+account+':'+labels[0]
    start=ledger.get(marker)
    if not start:
        ledger.put(marker,str(profile['historyId']))
        print('Gmail baseline established; existing labeled emails excluded',flush=True)
        return 0
    cursor=None; count=0
    for _ in range(20):
        params={'startHistoryId':start,'maxResults':100}
        if cursor: params['pageToken']=cursor
        # Do not filter history by label: label additions to older messages must be seen.
        data=client.get('history',params)
        ids=set()
        for entry in data.get('history',[]):
            for item in entry.get('labelsAdded',[]):
                if labels[0] in item.get('labelIds',[]): ids.add(item['message']['id'])
            for item in entry.get('messagesAdded',[]):
                if labels[0] in item['message'].get('labelIds',[]): ids.add(item['message']['id'])
        for mid in sorted(ids):
            message=client.get('messages/'+urllib.parse.quote(mid,safe=''),{'format':'full'})
            if labels[0] not in message.get('labelIds',[]): continue
            headers={h['name'].lower():h['value'] for h in message.get('payload',{}).get('headers',[])}
            name,address=email.utils.parseaddr(headers.get('from',''))
            # Outgoing/self-generated mail cannot turn into a new inbound request.
            if not address or address.lower()==account or 'SENT' in message.get('labelIds',[]): continue
            key='gmail:'+account+':'+mid
            timestamp='%.6f'%(int(message['internalDate'])/1000)
            source='https://mail.google.com/mail/u/'+urllib.parse.quote(account,safe='')+'/#all/'+message['threadId']
            text='Partner inbound email\nContact: %s\nEmail: %s\nSubject: %s\nBody:\n%s'%(name,address,headers.get('subject',''),body_text(message.get('payload',{}))[:16000])
            event=dict(kind='review_email',key=key,event_id=key,channel=cfg['shadow_channel'],ts=timestamp,
                       thread_ts=timestamp,text=text,source_url=source,source_type='gmail',email=address)
            count+=bool(store.enqueue(event))
        cursor=data.get('nextPageToken')
        if not cursor:
            # Only advance after every selected email has a durable enqueue receipt.
            ledger.put(marker,str(data['historyId']))
            return count
    raise ValueError('Gmail history exceeds page limit; checkpoint retained')
