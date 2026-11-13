"""Privacy-preserving, fail-open LangSmith tracing for Partner Ops.

The runtime intentionally records operational metadata only. Slack, Notion,
Gmail, Calendar, meeting, prompt, and model-output content is not sent to
LangSmith by this module.
"""
from contextlib import contextmanager
import hashlib
import os


TRUE_VALUES = {"1", "true", "yes", "on"}
SAFE_KEYS = {
    "action", "agent_id", "callback_type", "capture_count", "component",
    "confidence", "disposition", "enabled", "environment", "error_count",
    "error_type", "event_id_hash", "event_type", "existing_task_count",
    "flow_count", "model", "notification_count", "operation", "output_count",
    "project", "result_count", "review_required", "route", "source_id_hash",
    "source_type", "state", "status", "task_count", "thread_id_hash",
    "trace_version",
}


def tracing_enabled(environ=None):
    """Return whether tracing is explicitly enabled and authenticated."""
    env = environ or os.environ
    return (str(env.get("LANGSMITH_TRACING", "")).strip().lower() in TRUE_VALUES and
            bool(str(env.get("LANGSMITH_API_KEY", "")).strip()))


def hash_identifier(value):
    """Create a stable correlation value without exporting a source identifier."""
    if value is None or value == "":
        return ""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _safe_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def redact(values):
    """Allowlist operational fields and redact every unrecognized field."""
    if not isinstance(values, dict):
        return {"value": "[redacted]"}
    safe = {}
    for key, value in values.items():
        key = str(key)
        if key in SAFE_KEYS or key.endswith("_count") or key.endswith("_hash"):
            safe[key] = _safe_scalar(value)
        else:
            safe[key] = "[redacted]"
    return safe


class _NoopRun:
    def end(self, **_):
        return None


def finish_trace(run, outputs=None):
    """Record sanitized outputs without allowing telemetry to affect the agent."""
    try:
        run.end(outputs=redact(outputs or {}))
    except Exception:
        pass


@contextmanager
def trace_span(name, run_type="chain", inputs=None, tags=None, metadata=None):
    """Yield a LangSmith span when configured, otherwise a no-op span.

    SDK import, startup, and delivery failures are isolated from Partner Ops.
    Application exceptions still propagate unchanged.
    """
    if not tracing_enabled():
        yield _NoopRun()
        return
    try:
        from langsmith.run_helpers import trace
        environment = os.environ.get("LANGSMITH_AGENT_ENVIRONMENT", "local")
        agent_id = os.environ.get("LANGSMITH_AGENT_ID", "partner-ops")
        span = trace(
            name,
            run_type=run_type,
            inputs=redact(inputs or {}),
            project_name=os.environ.get("LANGSMITH_PROJECT") or None,
            tags=["partner-ops", environment] + list(tags or []),
            metadata=redact({
                "agent_id": agent_id,
                "environment": environment,
                "trace_version": "1",
                **(metadata or {}),
            }),
        )
        run = span.__enter__()
    except Exception:
        yield _NoopRun()
        return
    try:
        yield run
    except BaseException as exc:
        # Do not let the SDK serialize application exception messages because
        # they may contain source content. Record only the exception class.
        finish_trace(run, {"status": "error", "error_type": type(exc).__name__})
        try:
            span.__exit__(None, None, None)
        except Exception:
            pass
        raise
    else:
        try:
            span.__exit__(None, None, None)
        except Exception:
            pass
