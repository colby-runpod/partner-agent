import datetime as dt
import unittest

from p_ops import TaskEvidence, notion_patch, plan_task


TODAY = dt.date(2026, 10, 14)


def task(**changes):
    values = dict(task_id="task-1", title="Example task", status="In Progress", today=TODAY,
                  last_meaningful_activity=TODAY, owner="Colby", next_step="Review evidence",
                  next_step_date=TODAY, definition_of_done="Outcome confirmed")
    values.update(changes)
    return TaskEvidence(**values)


class TaskGovernanceTests(unittest.TestCase):
    def test_completion_evidence_allows_done(self):
        plan = plan_task(task(completion_evidence="Approved report confirms launch."))
        self.assertEqual(plan.proposed_status, "Done")
        self.assertTrue(plan.auto_update_allowed)
        self.assertFalse(plan.review_required)

    def test_inactivity_is_not_done(self):
        plan = plan_task(task(last_meaningful_activity=TODAY - dt.timedelta(days=10)))
        self.assertEqual(plan.proposed_status, "In Progress")
        self.assertFalse(plan.auto_update_allowed)

    def test_stale_task_closes_as_dropped(self):
        source = task(last_meaningful_activity=TODAY - dt.timedelta(days=21))
        plan = plan_task(source)
        self.assertEqual(plan.proposed_status, "Dropped")
        self.assertTrue(plan.auto_update_allowed)
        self.assertIn("record_stale_close_evidence", plan.actions)
        self.assertEqual(notion_patch(source, plan)["Close Reason"], "Stale 21d")

    def test_stale_guard_prevents_close(self):
        plan = plan_task(task(last_meaningful_activity=TODAY - dt.timedelta(days=30),
                              future_trigger="Partner call on October 20"))
        self.assertEqual(plan.proposed_status, "In Progress")
        self.assertFalse(plan.auto_update_allowed)
        self.assertTrue(plan.review_required)

    def test_dependency_moves_to_waiting(self):
        plan = plan_task(task(active_dependency="Finance approval", next_step="Follow up with Finance"))
        self.assertEqual(plan.proposed_status, "Waiting on Other")
        self.assertTrue(plan.auto_update_allowed)
        self.assertIn("prepare_follow_up", plan.actions)

    def test_explicit_snooze_is_preserved(self):
        plan = plan_task(task(snooze_until=TODAY + dt.timedelta(days=7)))
        self.assertEqual(plan.proposed_status, "Snoozed")
        self.assertTrue(plan.auto_update_allowed)

    def test_closed_task_is_never_reopened(self):
        plan = plan_task(task(status="Done", next_step="New request arrived"))
        self.assertEqual(plan.proposed_status, "Done")
        self.assertFalse(plan.auto_update_allowed)
        self.assertIn("create_successor_if_new_work_exists", plan.actions)

    def test_duplicate_is_dropped_and_linked(self):
        plan = plan_task(task(duplicate_of="canonical-7"))
        self.assertEqual(plan.proposed_status, "Dropped")
        self.assertIn("link_canonical_task", plan.actions)

    def test_missing_metadata_is_repaired_without_status_guess(self):
        plan = plan_task(task(owner="", next_step="", next_step_date=None, definition_of_done=""))
        self.assertEqual(plan.proposed_status, "In Progress")
        self.assertFalse(plan.auto_update_allowed)
        self.assertTrue(plan.review_required)
        self.assertEqual(set(plan.missing_fields), {"owner", "next_step", "next_step_date", "definition_of_done"})

    def test_due_date_is_never_rolled(self):
        original = TODAY - dt.timedelta(days=8)
        source = task(due_date=original)
        plan = plan_task(source)
        self.assertNotIn("roll_due_date", plan.actions)
        self.assertNotIn("Due Date", notion_patch(source, plan))

    def test_linear_issue_is_prepared_for_review(self):
        plan = plan_task(task(requires_engineering=True, linear_team="Integrations"))
        self.assertEqual(plan.linear_action, "prepare_issue_for_review")
        self.assertIn("prepare_linear_issue", plan.actions)
        self.assertFalse(plan.auto_update_allowed)

    def test_existing_linear_issue_is_monitored(self):
        source = task(requires_engineering=True, linear_issue_url="https://linear.app/runpod/issue/ENG-1")
        plan = plan_task(source)
        self.assertEqual(plan.linear_action, "monitor_linked_issue")
        self.assertIn("monitor_linear_issue", plan.actions)
        self.assertEqual(notion_patch(source, plan)["Linear Issue"], source.linear_issue_url)

    def test_done_patch_has_evidence_and_close_metadata(self):
        source = task(completion_evidence="Validated in the partner record.")
        patch = notion_patch(source, plan_task(source))
        self.assertEqual(patch["Status"], "Done")
        self.assertEqual(patch["Close Reason"], "Completed")
        self.assertEqual(patch["Closed At"], TODAY.isoformat())
        self.assertEqual(patch["Completion Evidence"], source.completion_evidence)

    def test_waiting_patch_starts_clock(self):
        source = task(active_dependency="Legal approval")
        patch = notion_patch(source, plan_task(source))
        self.assertEqual(patch["Status"], "Waiting on Other")
        self.assertEqual(patch["Waiting Since"], TODAY.isoformat())

    def test_review_patch_records_reason_without_changing_status(self):
        source = task(last_meaningful_activity=TODAY - dt.timedelta(days=28), material_risk="Partner commitment")
        patch = notion_patch(source, plan_task(source))
        self.assertTrue(patch["Review Required"])
        self.assertIn("prevents automatic closure", patch["Review Reason"])
        self.assertNotIn("Status", patch)


if __name__ == "__main__":
    unittest.main()
