from pathlib import Path
import sqlite3
import tempfile
import unittest
from intake import Store
from recovery import snapshot
from reconcile_state import inventory,compare


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'source.db'
        self.store=Store(self.path)

    def test_snapshot_matches_and_report_excludes_payload(self):
        self.store.enqueue({'key':'private-key','event_id':'private-event','text':'sensitive-message'})
        target=Path(self.temp.name)/'snapshot.db'
        snapshot(self.path,target)
        before=self.path.read_bytes()
        left=inventory(self.path,'intake'); right=inventory(target,'intake')
        self.assertTrue(compare(left,right)['matching'])
        self.assertFalse(left['ready_for_cutover'])
        self.assertNotIn('sensitive-message',str(left))
        self.assertNotIn('private-key',str(left))
        self.assertEqual(self.path.read_bytes(),before)

    def test_same_count_different_content_detected(self):
        self.store.enqueue({'key':'one','event_id':'one'})
        left=inventory(self.path,'intake')
        self.store.update('one',state='done')
        result=compare(left,inventory(self.path,'intake'))
        self.assertFalse(result['matching'])
        self.assertIn({'table':'intake_jobs','reason':'sha256_mismatch'},result['differences'])

    def test_uncertain_work_and_empty_history_are_blockers(self):
        self.assertIn({'reason':'no_recovered_history'},inventory(self.path,'intake')['issues'])
        self.store.enqueue({'key':'one','event_id':'one'})
        self.store.update('one',reply_state='sending')
        self.assertEqual(inventory(self.path,'intake')['issues'][0]['reason'],'unfinished_or_uncertain_work')

    def test_missing_tables_cannot_compare_as_equal(self):
        report=inventory(self.path)
        self.assertFalse(compare(report,report)['matching'])

    def test_outbox_uncertainty_and_missing_receipts_reported(self):
        from outbox import Outbox
        box=Outbox(self.path)
        with box.db() as db:
            db.execute("INSERT INTO outbox_delivery(id,snapshot,state) VALUES ('one','{}','sending')")
            db.execute("INSERT INTO outbox_delivery(id,snapshot,state) VALUES ('two','{}','posted')")
        reasons={x['reason'] for x in inventory(self.path)['issues']}
        self.assertIn('delivery_requires_reconciliation',reasons)
        self.assertIn('missing_slack_receipt',reasons)

    def test_missing_file_not_created(self):
        missing=Path(self.temp.name)/'missing.db'
        with self.assertRaises(ValueError): inventory(missing)
        self.assertFalse(missing.exists())

    def test_corrupt_file_rejected(self):
        broken=Path(self.temp.name)/'broken.db'; broken.write_text('not a database')
        with self.assertRaises(sqlite3.DatabaseError): inventory(broken)
