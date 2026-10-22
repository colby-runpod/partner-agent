"""One-time isolated DB initialization. No Slack, Notion or agent imports."""
import json
import os
import sys
import psycopg
from psycopg import sql
from postgres_store import SCHEMA


def main():
    admin=json.loads(os.environ['ADMIN_CREDENTIAL'])
    migration=json.loads(os.environ['MIGRATION_CREDENTIAL'])
    runtime=json.loads(os.environ['RUNTIME_CREDENTIAL'])
    settings=dict(host=os.environ['DB_HOST'],dbname='partneragent',sslmode='verify-full',
                  sslrootcert='/app/rds-ca.pem',connect_timeout=15)
    with psycopg.connect(**settings,user=admin['username'],password=admin['password']) as db:
        for name, credential in [('agent_migrator',migration),('agent_runtime',runtime)]:
            if credential['username'] != name:
                raise ValueError('Unexpected credential identity')
            exists=db.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(name,)).fetchone()
            # Do not change a previously provisioned role or silently reset its password.
            if not exists:
                db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}')
                           .format(sql.Identifier(name),sql.Literal(credential['password'])))
        db.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        db.execute('REVOKE ALL ON DATABASE partneragent FROM PUBLIC')
        db.execute('GRANT CONNECT ON DATABASE partneragent TO agent_migrator, agent_runtime')
        if not db.execute("SELECT 1 FROM pg_namespace WHERE nspname='partner_agent'").fetchone():
            # RDS administrators are not PostgreSQL superusers. Temporarily grant
            # membership to assign ownership, then revoke in the same transaction.
            member=db.execute("SELECT pg_has_role(current_user,'agent_migrator','MEMBER')").fetchone()[0]
            if not member:
                db.execute(sql.SQL('GRANT agent_migrator TO {}').format(sql.Identifier(admin['username'])))
            db.execute('CREATE SCHEMA partner_agent AUTHORIZATION agent_migrator')
            if not member:
                db.execute(sql.SQL('REVOKE agent_migrator FROM {}').format(sql.Identifier(admin['username'])))
        owner=db.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='partner_agent'").fetchone()[0]
        if owner != 'agent_migrator':
            raise ValueError('Unexpected existing schema owner')
        db.execute('REVOKE ALL ON SCHEMA partner_agent FROM PUBLIC')
        db.execute('GRANT USAGE ON SCHEMA partner_agent TO agent_runtime')
    with psycopg.connect(**settings,user=migration['username'],password=migration['password']) as db:
        db.execute(SCHEMA)
        db.execute('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA partner_agent TO agent_runtime')
        db.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA partner_agent TO agent_runtime')
        db.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA partner_agent GRANT SELECT, INSERT, UPDATE ON TABLES TO agent_runtime')
        db.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA partner_agent GRANT USAGE, SELECT ON SEQUENCES TO agent_runtime')
    with psycopg.connect(**settings,user=runtime['username'],password=runtime['password']) as db:
        rights=db.execute("SELECT has_schema_privilege(current_user,'partner_agent','CREATE'), has_table_privilege(current_user,'partner_agent.jobs','DELETE'), has_table_privilege(current_user,'partner_agent.jobs','INSERT')").fetchone()
        assert rights == (False,False,True), 'Unexpected runtime privileges'
        assert db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]
    print('PASS: schema initialized; restricted runtime login verified over TLS; no production jobs imported.',flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Database errors can quote SQL containing credentials. Never print their text.
        print('Database bootstrap failed: '+type(exc).__name__,file=sys.stderr,flush=True)
        raise SystemExit(1)
