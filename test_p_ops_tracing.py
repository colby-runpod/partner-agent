import os
import sys
import types
import unittest
from unittest.mock import patch

from p_ops_tracing import finish_trace, hash_identifier, redact, trace_span, tracing_enabled


class FakeRun:
    def __init__(self): self.outputs = None
    def end(self, outputs=None): self.outputs = outputs


class FakeTrace:
    calls = []
    exits = []
    def __init__(self, name, **kwargs):
        self.run = FakeRun()
        self.calls.append((name, kwargs, self.run))
    def __enter__(self): return self.run
    def __exit__(self, *values): self.exits.append(values)


class TracingTests(unittest.TestCase):
    def setUp(self):
        FakeTrace.calls.clear()
        FakeTrace.exits.clear()

    def modules(self):
        package = types.ModuleType("langsmith")
        helpers = types.ModuleType("langsmith.run_helpers")
        helpers.trace = FakeTrace
        return {"langsmith": package, "langsmith.run_helpers": helpers}

    def test_requires_explicit_flag_and_api_key(self):
        self.assertFalse(tracing_enabled({}))
        self.assertFalse(tracing_enabled({"LANGSMITH_TRACING": "true"}))
        self.assertTrue(tracing_enabled({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "secret"}))

    def test_redacts_unrecognized_content_and_hashes_identifiers(self):
        value = redact({"status": "complete", "message": "private Slack text",
                        "task_count": 2, "source_id_hash": "abc"})
        self.assertEqual("complete", value["status"])
        self.assertEqual("[redacted]", value["message"])
        self.assertEqual(2, value["task_count"])
        self.assertEqual(hash_identifier("WI-123"), hash_identifier("WI-123"))
        self.assertNotIn("WI-123", hash_identifier("WI-123"))

    def test_span_records_only_allowlisted_metadata_and_outputs(self):
        env = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "secret",
               "LANGSMITH_PROJECT": "partner-ops-local", "LANGSMITH_AGENT_ID": "partner-ops",
               "LANGSMITH_AGENT_ENVIRONMENT": "local"}
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, self.modules()):
            with trace_span("partner_ops_test", inputs={"status": "queued", "text": "private"}) as run:
                finish_trace(run, {"status": "complete", "message": "private output"})
        name, kwargs, run = FakeTrace.calls[0]
        self.assertEqual("partner_ops_test", name)
        self.assertEqual("partner-ops-local", kwargs["project_name"])
        self.assertEqual({"status": "queued", "text": "[redacted]"}, kwargs["inputs"])
        self.assertEqual("partner-ops", kwargs["metadata"]["agent_id"])
        self.assertEqual("local", kwargs["metadata"]["environment"])
        self.assertEqual({"status": "complete", "message": "[redacted]"}, run.outputs)

    def test_tracing_failure_does_not_change_application_result(self):
        class BrokenTrace:
            def __init__(self, *_args, **_kwargs): pass
            def __enter__(self): raise OSError("unavailable")
        modules = self.modules()
        modules["langsmith.run_helpers"].trace = BrokenTrace
        env = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "secret"}
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules):
            with trace_span("partner_ops_test") as run:
                finish_trace(run, {"status": "complete"})

    def test_application_exception_is_re_raised_without_its_message(self):
        env = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "secret"}
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, self.modules()):
            with self.assertRaisesRegex(ValueError, "private source content"):
                with trace_span("partner_ops_test"):
                    raise ValueError("private source content")
        self.assertEqual("error", FakeTrace.calls[0][2].outputs["status"])
        self.assertEqual("ValueError", FakeTrace.calls[0][2].outputs["error_type"])
        self.assertEqual((None, None, None), FakeTrace.exits[0])


if __name__ == "__main__":
    unittest.main()
