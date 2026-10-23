"""Additive pilot migration. Migration/runtime secrets only; no administrator login."""
import json
import os
import psycopg
from postgres_store import SCHEMA


def main():
    migration=json.loads(os.environ['MIGRATION_CREDENTIAL'])
    runtime=json.loads(os.environ['RUNTIME_CREDENTIAL'])
    if migration['username']!='agent_migrator' or runtime['username']!='agent_runtime':
        raise ValueError('Unexpected database role')
    settings=dict(host=os.environ['DB_HOST'],dbname='partneragent',sslmode='verify-full',
                  sslrootcert='/app/rds-ca.pem',connect_timeout=10)
    with psycopg.connect(**settings,user=migration['username'],password=migration['password']) as db:
        db.execute("SET LOCAL lock_timeout='5s'")
        db.execute("SET LOCAL statement_timeout='30s'")
        db.execute(SCHEMA)
        db.execute('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA partner_agent TO agent_runtime')
        db.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA partner_agent TO agent_runtime')
    required={'jobs','reviews','review_meta','feedback','outbox_delivery','outbox_events','worker_ownership'}
    with psycopg.connect(**settings,user=runtime['username'],password=runtime['password']) as db:
        db.execute('SET TRANSACTION READ ONLY')
        assert db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]
        for table in sorted(required):
            db.execute('SELECT * FROM partner_agent.'+table+' LIMIT 0')
            rights=db.execute('SELECT has_table_privilege(current_user,%s,\'DELETE\'), has_table_privilege(current_user,%s,\'INSERT\')',('partner_agent.'+table,'partner_agent.'+table)).fetchone()
            assert rights==(False,True)
    print('PASS: seven tables accessible; runtime privileges restricted; TLS verified. No business records changed.',flush=True)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('Pilot migration failed: '+type(exc).__name__,flush=True)
        raise SystemExit(1)
