"""Explicit storage selection; PostgreSQL never falls back to local SQLite."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from storage_errors import StorageUnavailable


def backend():
    value=os.environ.get('STORAGE_BACKEND','sqlite')
    if value not in {'sqlite','postgres'}:
        raise ValueError('STORAGE_BACKEND must be sqlite or postgres')
    return value


def postgres_settings():
    if os.environ.get('POSTGRES_STATE_READY') != 'true':
        raise ValueError('PostgreSQL requires reconciled state: POSTGRES_STATE_READY=true')
    host=os.environ.get('DB_HOST','')
    team=os.environ.get('SLACK_TEAM_ID','')
    ca=os.environ.get('DB_SSL_ROOT_CERT','/app/rds-ca.pem')
    if not host or not team or ':' in team or not Path(ca).is_file():
        raise ValueError('PostgreSQL requires DB_HOST, SLACK_TEAM_ID and a trusted CA file')
    try:
        credential=json.loads(os.environ.get('RUNTIME_CREDENTIAL',''))
        if credential['username'] != 'agent_runtime' or not isinstance(credential['password'],str) or not credential['password']:
            raise ValueError()
    except (ValueError,KeyError,TypeError):
        raise ValueError('RUNTIME_CREDENTIAL must contain the restricted runtime credentials') from None
    return dict(host=host,dbname='partneragent',user='agent_runtime',password=credential['password'],
                sslmode='verify-full',sslrootcert=ca),team+':U0C7372ET45'


def make_store(path):
    if backend() == 'sqlite':
        from intake import Store
        return Store(path)
    settings,namespace=postgres_settings()
    from postgres_store import PostgresStore
    import psycopg
    store=PostgresStore(settings,namespace)
    try:
        with store.connect() as db:
            for table in ('jobs','reviews','review_meta','feedback','outbox_delivery','outbox_events','worker_ownership'):
                db.execute('SELECT * FROM partner_agent.'+table+' LIMIT 0')
    except psycopg.Error:
        raise StorageUnavailable('PostgreSQL schema or connection unavailable') from None
    return store


@contextmanager
def worker_store(path,role='queue'):
    store=make_store(path)
    if backend() == 'postgres':
        from postgres_worker import worker_session
        with worker_session(store,role) as owned:
            yield owned
    else:
        import fcntl
        suffix='.outbox.lock' if role=='outbox' else '.worker.lock'
        with open(str(path)+suffix,'w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield store


def make_ledger(store):
    if backend() == 'postgres':
        from postgres_ledger import PostgresLedger
        return PostgresLedger(store)
    from review import Ledger
    return Ledger(store.path)


def make_outbox(store):
    from outbox import Outbox
    if backend() == 'postgres':
        return Outbox(None,storage=store)
    return Outbox(store.path)


def owns_message(channel,ts):
    import psycopg
    try:
        return make_store(None).owns(channel,ts)
    except psycopg.Error:
        raise StorageUnavailable('Receipt lookup unavailable') from None
