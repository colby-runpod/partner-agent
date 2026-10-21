import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from recovery import snapshot


class RecoveryTests(unittest.TestCase):
    def test_backup_and_restore_include_wal_and_delivery_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup, restored = [root / name for name in ('live.db', 'backup.db', 'restored.db')]
            with sqlite3.connect(source) as live:
                live.execute('PRAGMA journal_mode=WAL')
                live.execute('CREATE TABLE receipts (id TEXT PRIMARY KEY, ts TEXT)')
                live.execute("INSERT INTO receipts VALUES ('OUT-TEST', '123.456')")
                live.commit()
                snapshot(source, backup)
            snapshot(backup, restored)
            with sqlite3.connect(restored) as db:
                self.assertEqual(db.execute('SELECT * FROM receipts').fetchall(), [('OUT-TEST', '123.456')])
            self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                snapshot(source, restored)

    def test_missing_or_corrupt_source_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                snapshot(root / 'missing', root / 'backup')
            (root / 'corrupt').write_text('not a database')
            with self.assertRaises(sqlite3.DatabaseError):
                snapshot(root / 'corrupt', root / 'backup')
            self.assertFalse((root / 'backup').exists())
