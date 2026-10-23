"""Read-only inventory of recovered agent SQLite snapshots. Never enables processing."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

TABLES = {
    'intake_jobs': ('key','event_id','payload','state','ref','result_url','reply','reply_state','error'),
    'reviews': ('key','channel','ts','created','company','route','score','decision','headline','outcome','delivered',
                'card_ts','data','card_text','notion_url','notion_id','notes','parked_by'),
    'meta': ('k','v'),
    'feedback': ('id','review_key','user','vote','value','created'),
    'outbox_delivery': ('id','snapshot','state','ts','decision','actor','at','error'),
    'outbox_events': ('key','payload','done'),
}
PROFILES = {'intake': ('intake_jobs',), 'review': ('intake_jobs','reviews','meta','feedback'),
            'combined': tuple(TABLES)}


def fingerprint(rows):
    # Hash canonical row encodings in sorted order; neither content nor IDs leave the report.
    encoded=sorted(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=True) for row in rows)
    digest=hashlib.sha256()
    for row in encoded:
        raw=row.encode()
        digest.update(len(raw).to_bytes(8,'big')); digest.update(raw)
    return digest.hexdigest()


def inventory(source,profile='combined'):
    if profile not in PROFILES: raise ValueError('Unknown database profile')
    path=Path(source).resolve()
    if not path.is_file(): raise ValueError('Snapshot file does not exist')
    report={'format':1,'profile':profile,'ready_for_cutover':False,'tables':{},'issues':[],
            'limitations':['A consistent snapshot is not proof that former workers are stopped.',
                          'AWS comparison, historical recovery, and operator review are still required.']}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if [tuple(x) for x in db.execute('PRAGMA integrity_check')] != [('ok',)]:
            raise ValueError('Snapshot integrity check failed')
        names={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in PROFILES[profile]:
            if table not in names:
                report['issues'].append({'table':table,'reason':'missing_table'}); continue
            columns={x['name'] for x in db.execute('PRAGMA table_info("'+table+'")')}
            missing=sorted(set(TABLES[table])-columns)
            if missing:
                report['issues'].append({'table':table,'reason':'missing_columns','columns':missing})
            selected=[c for c in TABLES[table] if c in columns]
            if not selected: continue
            rows=[dict(r) for r in db.execute('SELECT '+','.join('"'+c+'"' for c in selected)+' FROM "'+table+'"')]
            item={'count':len(rows),'columns':selected,'sha256':fingerprint(rows)}
            if 'state' in selected:
                # Do not echo unexpected state text from untrusted records.
                known={'pending','processing','writing','done','failed','review','sending','sync','deciding','posted','settled','cancelled'}
                item['states']=dict(Counter(r['state'] if r['state'] in known else 'unknown' for r in rows))
            if table=='intake_jobs':
                uncertain=sum(r.get('state') in ('processing','writing','review') or r.get('reply_state') in ('sending','review') for r in rows)
                if uncertain: report['issues'].append({'table':table,'reason':'unfinished_or_uncertain_work','count':uncertain})
            if table=='outbox_delivery':
                uncertain=sum(r.get('state') in ('sending','sync','deciding') for r in rows)
                if uncertain: report['issues'].append({'table':table,'reason':'delivery_requires_reconciliation','count':uncertain})
                missing_receipts=sum(r.get('state') in ('posted','settled') and not r.get('ts') for r in rows)
                if missing_receipts: report['issues'].append({'table':table,'reason':'missing_slack_receipt','count':missing_receipts})
            if table=='outbox_events':
                item['pending']=sum(not r.get('done') for r in rows)
            report['tables'][table]=item
    if not any(t['count'] for t in report['tables'].values()):
        report['issues'].append({'reason':'no_recovered_history'})
    return report


def compare(left,right):
    if left.get('format')!=1 or right.get('format')!=1 or left.get('profile')!=right.get('profile'):
        raise ValueError('Incompatible inventory reports')
    differences=[]
    for table in PROFILES[left['profile']]:
        a,b=left['tables'].get(table),right['tables'].get(table)
        if a is None or b is None:
            differences.append({'table':table,'reason':'missing_table'}); continue
        for field in ('columns','count','sha256'):
            if a[field]!=b[field]: differences.append({'table':table,'reason':field+'_mismatch'})
    return {'format':1,'matching':not differences,'ready_for_cutover':False,'differences':differences,
            'left_issues':left['issues'],'right_issues':right['issues']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--profile',choices=PROFILES,default='combined')
    parser.add_argument('--compare-to',help='Second SQLite snapshot; both inputs remain read-only')
    parser.add_argument('--output',required=True,help='New private report file; never overwritten')
    args=parser.parse_args()
    report=inventory(args.source,args.profile)
    if args.compare_to: report=compare(report,inventory(args.compare_to,args.profile))
    fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as output: json.dump(report,output,indent=2); output.write('\n')
    print('Private reconciliation report written. Processing remains disabled.')


if __name__=='__main__':
    try: main()
    except (ValueError,sqlite3.Error,OSError):
        raise SystemExit('Reconciliation failed. Check snapshot integrity and paths; no processing was enabled.') from None
