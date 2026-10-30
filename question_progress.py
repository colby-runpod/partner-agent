"""Best-effort reaction lifecycle on accepted human requests, never an approval signal."""
import json
import os
import urllib.request

ICONS=('eyes','hourglass_flowing_sand','white_check_mark','warning')


def change(event,method,name,timeout=1):
    token=os.environ.get('SLACK_BOT_TOKEN')
    if not token or not event.get('progress'):return False
    try:
        request=urllib.request.Request('https://slack.com/api/reactions.'+method,
            data=json.dumps(dict(channel=event['channel'],timestamp=event['ts'],name=name)).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=timeout) as response:result=json.load(response)
        ok=result.get('ok') or result.get('error') in ('already_reacted','no_reaction')
        if not ok:print('Question reaction unavailable: '+str(result.get('error','unknown')),flush=True)
        return bool(ok)
    except Exception:
        print('Question reaction unavailable',flush=True)
        return False


def received(store,event):
    # At most 0.8 seconds of Slack I/O in the signed webhook, after durable enqueue.
    # Duplicate deliveries never invoke this. A late acknowledgment cannot regress progress.
    if not event.get('progress'):return
    try:
        if store.get(event['key'])['state']!='pending':return
        change(event,'add','eyes',timeout=.4)
        if store.get(event['key'])['state']!='pending':change(event,'remove','eyes',timeout=.4)
    except Exception:pass  # Cosmetic status must not change successful queue acknowledgment.


def status(event,icon):
    if not event.get('progress') or icon not in ICONS:return
    # Add first so a permission/network failure doesn't remove the last visible status.
    if change(event,'add',icon):
        for other in ICONS:
            if other!=icon:change(event,'remove',other)


def working(event):status(event,'hourglass_flowing_sand')


def finished(store,key,event):
    if not event.get('progress'):return
    job=store.get(key)
    if job['reply_state']=='sent':status(event,'warning' if job.get('error') else 'white_check_mark')
    elif job['reply_state']=='review':status(event,'warning')
