"""Deterministic p-OPS task hygiene and routing foundation.

This module plans safe task updates. It does not call Notion, Linear, Gmail,
Slack, HubSpot, or any model. External writes and communication remain disabled.
"""
from dataclasses import asdict, dataclass, field
import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Optional


ACTIVE = {"Inbox", "Next", "In Progress", "Waiting on Other", "Snoozed", "Finalizing"}
CLOSED = {"Done", "Dropped"}
WORK_MODES = {"Execute", "Decide", "Delegate", "Prepare", "Meeting-bound", "Waiting", "Monitor", "Reference"}
NOTION_DATA_SOURCE_ID = "e77648f6-502d-450f-8b79-4dd2ced52bab"


@dataclass(frozen=True)
class TaskEvidence:
    task_id: str
    title: str
    status: str
    today: dt.date
    last_meaningful_activity: Optional[dt.date] = None
    completion_evidence: str = ""
    explicit_drop_evidence: str = ""
    duplicate_of: str = ""
    future_trigger: str = ""
    active_dependency: str = ""
    unresolved_commitment: str = ""
    material_risk: str = ""
    someone_waiting: str = ""
    snooze_until: Optional[dt.date] = None
    owner: str = ""
    next_step: str = ""
    next_step_date: Optional[dt.date] = None
    due_date: Optional[dt.date] = None
    definition_of_done: str = ""
    work_mode: str = ""
    objective: str = ""
    workstream: str = ""
    partner: str = ""
    requires_engineering: bool = False
    linear_issue_url: str = ""
    linear_team: str = ""


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    current_status: str
    proposed_status: str
    auto_update_allowed: bool
    reason: str
    review_required: bool
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    actions: tuple[str, ...] = field(default_factory=tuple)
    linear_action: str = "none"
    close_reason: str = ""

    def to_dict(self):
        return asdict(self)


def _validate(task: TaskEvidence):
    if not task.task_id or not task.title.strip():
        raise ValueError("Every task needs an ID and title.")
    if task.status not in ACTIVE | CLOSED:
        raise ValueError("Unsupported status: " + task.status)
    if task.work_mode and task.work_mode not in WORK_MODES:
        raise ValueError("Unsupported work mode: " + task.work_mode)
    for value in (task.last_meaningful_activity, task.snooze_until, task.next_step_date, task.due_date):
        if value and value > task.today + dt.timedelta(days=3650):
            raise ValueError("Date is implausibly far in the future.")


def _missing(task: TaskEvidence):
    missing = []
    if task.status in ACTIVE:
        for name, value in (("owner", task.owner), ("next_step", task.next_step)):
            if not value.strip():
                missing.append(name)
        if not task.next_step_date and task.status not in {"Waiting on Other", "Snoozed"}:
            missing.append("next_step_date")
        if not task.definition_of_done.strip() and task.status not in {"Inbox"}:
            missing.append("definition_of_done")
    return tuple(missing)


def _linear_action(task: TaskEvidence):
    if not task.requires_engineering:
        return "none"
    if task.linear_issue_url:
        return "monitor_linked_issue"
    if not task.linear_team.strip():
        return "identify_team_before_draft"
    return "prepare_issue_for_review"


def plan_task(task: TaskEvidence) -> TaskPlan:
    """Return an evidence-bound plan without performing a write."""
    _validate(task)
    missing = _missing(task)
    linear = _linear_action(task)

    if task.status in CLOSED:
        actions = ("create_successor_if_new_work_exists",) if any(
            (task.next_step.strip(), task.future_trigger.strip(), task.active_dependency.strip())) else ()
        return TaskPlan(task.task_id, task.status, task.status, False,
                        "Closed tasks are preserved and never reopened automatically.",
                        bool(actions), (), actions, linear, "")

    if task.completion_evidence.strip():
        return TaskPlan(task.task_id, task.status, "Done", True,
                        "Completion evidence confirms the intended outcome.", False,
                        (), ("record_completion_evidence",), linear, "Completed")

    if task.duplicate_of.strip():
        return TaskPlan(task.task_id, task.status, "Dropped", True,
                        "The task is a duplicate of the recorded canonical task.", False,
                        (), ("link_canonical_task", "record_drop_reason"), linear, "Duplicate")

    if task.explicit_drop_evidence.strip():
        return TaskPlan(task.task_id, task.status, "Dropped", True,
                        "An approved source explicitly canceled or deprioritized the work.", False,
                        (), ("record_drop_reason",), linear, "No longer relevant")

    if task.snooze_until and task.snooze_until > task.today:
        return TaskPlan(task.task_id, task.status, "Snoozed", True,
                        "The task has an explicit future restart date.", False,
                        (), ("preserve_restart_trigger",), linear, "")

    if task.active_dependency.strip():
        return TaskPlan(task.task_id, task.status, "Waiting on Other", True,
                        "Progress depends on the recorded external or cross-functional dependency.", False,
                        missing, ("set_checkpoint", "prepare_follow_up"), linear, "")

    guards = (task.future_trigger, task.unresolved_commitment, task.material_risk, task.someone_waiting)
    if task.last_meaningful_activity:
        age = (task.today - task.last_meaningful_activity).days
        if age >= 21 and not any(value.strip() for value in guards):
            return TaskPlan(task.task_id, task.status, "Dropped", True,
                            "No meaningful activity for %d days and no stale-close guard applies." % age,
                            False, (), ("record_stale_close_evidence",), linear, "Stale 21d")
        if age >= 21:
            return TaskPlan(task.task_id, task.status, task.status, False,
                            "The task is stale, but a commitment, trigger, risk, or waiting stakeholder prevents automatic closure.",
                            True, missing, ("surface_stale_exception",), linear, "")

    actions = []
    if missing:
        actions.append("repair_missing_metadata")
    if linear == "prepare_issue_for_review":
        actions.append("prepare_linear_issue")
    elif linear == "identify_team_before_draft":
        actions.append("identify_linear_team")
    elif linear == "monitor_linked_issue":
        actions.append("monitor_linear_issue")
    return TaskPlan(task.task_id, task.status, task.status, False,
                    "No evidence supports an automatic status transition.", bool(missing),
                    missing, tuple(actions), linear, "")


def notion_patch(task: TaskEvidence, plan: TaskPlan):
    """Build a preview of exact Working Tasks property changes.

    The caller must separately apply and reconcile the write. Due Date is never
    included because p-OPS does not roll commitments to hide overdue work.
    """
    if task.task_id != plan.task_id:
        raise ValueError("Task and plan IDs do not match.")
    patch = {"Last Automation Review": task.today.isoformat()}
    if plan.review_required:
        patch["Review Required"] = True
        patch["Review Reason"] = plan.reason
    if plan.auto_update_allowed and plan.proposed_status != plan.current_status:
        patch["Status"] = plan.proposed_status
        if plan.proposed_status in CLOSED:
            patch["Closed At"] = task.today.isoformat()
            patch["Close Reason"] = plan.close_reason
            if plan.proposed_status == "Done":
                patch["Completion Evidence"] = task.completion_evidence.strip()
        elif plan.proposed_status == "Waiting on Other" and task.status != "Waiting on Other":
            patch["Waiting Since"] = task.today.isoformat()
    if task.linear_issue_url:
        patch["Linear Issue"] = task.linear_issue_url
    return patch


def _date(value):
    return dt.date.fromisoformat(value) if value else None


def from_dict(raw):
    data = dict(raw)
    for key in ("today", "last_meaningful_activity", "snooze_until", "next_step_date", "due_date"):
        if key == "today":
            data[key] = _date(data[key])
        elif key in data:
            data[key] = _date(data[key])
    return TaskEvidence(**data)


def main():
    parser = argparse.ArgumentParser(description="Preview p-OPS task hygiene plans. No external writes occur.")
    parser.add_argument("input", type=Path, help="JSON array of normalized task evidence")
    args = parser.parse_args()
    tasks = json.loads(args.input.read_text())
    if not isinstance(tasks, list):
        raise SystemExit("Input must be a JSON array.")
    print(json.dumps([plan_task(from_dict(row)).to_dict() for row in tasks], indent=2, default=str))


if __name__ == "__main__":
    main()
