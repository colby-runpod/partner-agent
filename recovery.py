"""Portable SQLite recovery. Stop ALL writers before restoring or taking a multi-file snapshot."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError('Source database does not exist')
    if destination.exists() or source == destination:
        raise ValueError('Destination must be a new file')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=destination.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode=DELETE")
                if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Database integrity check failed')
        with open(temporary, 'rb') as saved:
            os.fsync(saved.fileno())
        # Link publishes atomically and refuses to overwrite an existing destination.
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('source')
    parser.add_argument('destination', help='New path; existing databases are never overwritten')
    args = parser.parse_args()
    snapshot(args.source, args.destination)
    print('Verified database copy created. Keep it private and encrypt off-host copies.')


if __name__ == '__main__':
    main()
