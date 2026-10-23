"""Exclusive worker sessions with durable crash holds, not expiring leases.

A lost connection must never let a replacement race a still-running HTTP call.
Only normal completion releases ownership. Crashes require operator reconciliation.
"""
from contextlib import contextmanager
import hashlib
import threading
import uuid
import psycopg
from postgres_store import PostgresStore


class WorkerBusy(RuntimeError):
    pass


class WorkerStopped(BaseException):
    """Fatal: ordinary per-job exception handlers must not swallow loss of storage."""


class WorkerStore(PostgresStore):
    def __init__(self, source, db):
        super().__init__(source.connection, source.namespace)
        self._db = db
        self._thread = threading.get_ident()
        self._stopped = False
        self._finished = False

    @contextmanager
    def connect(self):
        if self._stopped or self._finished or threading.get_ident() != self._thread:
            self._stopped = True
            raise WorkerStopped('Worker session is unavailable')
        try:
            # Every operation uses the owning session; never reconnect silently.
            with self._db.transaction():
                yield self._db
        except psycopg.Error:
            self._stopped = True
            raise WorkerStopped('Worker database session failed') from None

    def check(self):
        with self.connect() as db:
            db.execute('SELECT 1')


@contextmanager
def worker_session(source, role):
    if role not in {'queue','outbox'}:
        raise ValueError('Unknown worker role')
    lock_key=int.from_bytes(hashlib.sha256((source.namespace+':'+role).encode()).digest()[:8],
                            'big',signed=True)
    owner=str(uuid.uuid4())
    db=source.connect()
    db.autocommit=True
    worker=None
    try:
        acquired=db.execute('SELECT pg_try_advisory_lock(%s) AS acquired',(lock_key,)).fetchone()['acquired']
        if not acquired:
            raise WorkerBusy('Another worker owns this queue')
        with db.transaction():
            claimed=db.execute('''INSERT INTO partner_agent.worker_ownership(namespace,role,owner,active)
                VALUES (%s,%s,%s,TRUE) ON CONFLICT(namespace,role) DO UPDATE SET
                owner=EXCLUDED.owner,active=TRUE,started_at=CURRENT_TIMESTAMP
                WHERE worker_ownership.active=FALSE RETURNING owner''',(source.namespace,role,owner)).fetchone()
            if not claimed:
                raise WorkerBusy('Previous worker requires reconciliation before restart')
        worker=WorkerStore(source,db)
        yield worker
        # Reached only on normal exit. A swallowed failure still prevents release.
        worker.check()
        with db.transaction():
            released=db.execute('''UPDATE partner_agent.worker_ownership SET active=FALSE
                WHERE namespace=%s AND role=%s AND owner=%s AND active=TRUE''',
                (source.namespace,role,owner)).rowcount
            if released != 1:
                raise WorkerStopped('Worker ownership changed unexpectedly')
    finally:
        if worker is not None: worker._finished=True
        db.close()  # Releases advisory lock, but never clears a crash hold.
