"""Credential-free corpus validation and optional live p-OPS triage evaluation."""
import argparse
import json
from pathlib import Path

from p_ops_triage import ResponsesTriageClient, guard_decision


REQUIRED_CASE_FIELDS = {"id", "name", "provenance", "source", "expected"}


def load_cases(path):
    cases = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON on eval line %d" % number) from None
        validate_case(case)
        cases.append(case)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Eval case IDs must be unique.")
    if not cases:
        raise ValueError("Eval corpus is empty.")
    return cases


def validate_case(case):
    if not REQUIRED_CASE_FIELDS <= set(case):
        raise ValueError("Eval case is missing required fields.")
    if case["provenance"] not in {"historical", "policy_edge"}:
        raise ValueError("Eval provenance must be historical or policy_edge.")
    source, expected = case["source"], case["expected"]
    if not source.get("id") or not source.get("text"):
        raise ValueError("Eval source requires an ID and text.")
    if not expected.get("dispositions") or not isinstance(expected.get("review_required"), bool):
        raise ValueError("Eval expectation requires dispositions and review_required.")


def bundle_from_case(case):
    return {"source": case["source"], "existing_tasks": case.get("existing_tasks", []),
            "meetings": case.get("meetings", []), "objectives": case.get("objectives", []),
            "workstreams": case.get("workstreams", []), "partners": case.get("partners", []),
            "explicit_deadlines": case.get("explicit_deadlines", []),
            "risk_classes": case.get("risk_classes", []),
            "explicit_cancellation": bool(case.get("explicit_cancellation")),
            "current_date": case.get("current_date", "2026-10-15")}


def score_case(case, guarded):
    expected, failures = case["expected"], []
    if guarded.decision.disposition not in expected["dispositions"]:
        failures.append("disposition")
    if guarded.review_required != expected["review_required"]:
        failures.append("review_required")
    for reason in expected.get("required_policy_reasons", []):
        if not any(value == reason or value.startswith(reason + ":") for value in guarded.policy_reasons):
            failures.append("missing_policy_reason:" + reason)
    for violation in expected.get("forbidden_violations", []):
        if violation in guarded.violations:
            failures.append("forbidden_violation:" + violation)
    if expected.get("canonical_task_id") != guarded.decision.canonical_task_id:
        if "canonical_task_id" in expected:
            failures.append("canonical_task_id")
    return {"id": case["id"], "name": case["name"], "passed": not failures,
            "failures": failures, "disposition": guarded.decision.disposition,
            "review_required": guarded.review_required, "policy_reasons": list(guarded.policy_reasons),
            "violations": list(guarded.violations)}


def run_live(cases, client):
    results = []
    for case in cases:
        bundle = bundle_from_case(case)
        try:
            decision = client.analyze(bundle)
            results.append(score_case(case, guard_decision(decision, bundle, pilot=True)))
        except Exception as exc:
            results.append({"id": case["id"], "name": case["name"], "passed": False,
                            "failures": ["runner_error:" + str(exc)]})
    return results


def summary(cases, results=None):
    historical = sum(case["provenance"] == "historical" for case in cases)
    base = {"cases": len(cases), "historical": historical, "policy_edge": len(cases) - historical,
            "corpus_valid": True, "live_model_run": results is not None}
    if results is not None:
        base.update({"passed": sum(row["passed"] for row in results),
                     "failed": sum(not row["passed"] for row in results), "results": results})
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/p_ops_triage_cases.jsonl"))
    parser.add_argument("--live", action="store_true", help="Call the configured model; may incur API charges")
    parser.add_argument("--output", type=Path, default=Path("output/p_ops_triage_eval.json"))
    args = parser.parse_args()
    cases = load_cases(args.cases)
    report = summary(cases, run_live(cases, ResponsesTriageClient()) if args.live else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if report.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
