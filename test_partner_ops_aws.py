from pathlib import Path
import unittest


ROOT = Path(__file__).parent


class PartnerOpsAwsTests(unittest.TestCase):
    def test_foundation_is_isolated_and_persistent(self):
        template = (ROOT / "infra" / "partner-ops-foundation.yaml").read_text()
        self.assertIn("RepositoryName: partner-ops-agent", template)
        self.assertIn("Encrypted: true", template)
        self.assertIn("BackupPolicy:", template)
        self.assertIn("Path: /partner-ops", template)
        self.assertIn("Name: partner-ops/slack-bot-token", template)
        self.assertIn("Name: partner-ops/slack-signing-secret", template)
        self.assertIn("Name: partner-ops/notion-token", template)
        self.assertIn("Name: partner-ops/anthropic-api-key", template)
        self.assertIn("Name: partner-ops/langsmith-api-key", template)
        self.assertIn("LangSmithApiKeySecretArn:", template)
        self.assertNotIn("team-partner-agent/pilot/slack", template)

    def test_deployment_is_hard_limited_to_disabled_partner_ops(self):
        script = (ROOT / "scripts" / "deploy_partner_ops_aws.py").read_text()
        self.assertIn('PARTNER_OPS_APP_ID = "A0CA2C43D4L"', script)
        self.assertIn('"minTaskCount": 1, "maxTaskCount": 1', script)
        self.assertIn('"P_OPS_WORKING_TASK_BRIEF_ENABLED"', script)
        self.assertIn('[{"name": name, "value": "false"} for name in disabled]', script)
        self.assertIn('"command": ["python", "p_ops_service.py"]', script)
        self.assertIn('"containerPath": "/data"', script)
        self.assertNotIn("A0C76FQTM62", script)
        self.assertIn('"LANGSMITH_TRACING", "value": "true" if langsmith_tracing else "false"', script)
        self.assertIn('"LANGSMITH_PROJECT", "value": "partner-ops-production"', script)
        self.assertIn('"LANGSMITH_AGENT_ENVIRONMENT", "value": "production"', script)
        self.assertIn('"--enable-langsmith-tracing"', script)

    def test_capture_pilot_is_explicit_and_allowlisted(self):
        script = (ROOT / "scripts" / "deploy_partner_ops_aws.py").read_text()
        self.assertIn('CAPTURE_TEST_CHANNEL_ID = "C0C7HG8KYFL"', script)
        self.assertIn('CAPTURE_TEST_USER_ID = "U0BEHPEJA6L"', script)
        self.assertIn('"--enable-capture-test"', script)
        self.assertIn('"P_OPS_CAPTURE_ENABLED", "value": "true" if capture_test else "false"', script)
        self.assertIn('"capture-test" if args.enable_capture_test else "disabled"', script)
        self.assertIn('capture_test=args.enable_capture_test', script)

    def test_shadow_triage_pilot_is_explicit_and_cannot_write(self):
        script = (ROOT / "scripts" / "deploy_partner_ops_aws.py").read_text()
        self.assertIn('SHADOW_TRIAGE_MODEL = "claude-sonnet-4-6"', script)
        self.assertIn('"--enable-shadow-triage-test"', script)
        self.assertIn('Shadow triage requires --enable-capture-test.', script)
        self.assertIn('"P_OPS_TRIAGE_SHADOW_ENABLED", "value": "true" if shadow_triage_test else "false"', script)
        self.assertIn('{"name": "NOTION_TOKEN", "valueFrom": outputs["NotionTokenSecretArn"]}', script)
        self.assertIn('{"name": "ANTHROPIC_API_KEY", "valueFrom": outputs["AnthropicApiKeySecretArn"]}', script)
        self.assertIn('"P_OPS_NOTION_WRITE"', script)
        self.assertIn('"P_OPS_SEND_MESSAGES"', script)

    def test_slack_review_pilot_is_thread_scoped_and_cannot_execute(self):
        script = (ROOT / "scripts" / "deploy_partner_ops_aws.py").read_text()
        self.assertIn('"--enable-slack-review-test"', script)
        self.assertIn("Slack review requires both capture and shadow triage test stages.", script)
        self.assertIn('"P_OPS_REVIEW_CHANNEL_ID", "value": CAPTURE_TEST_CHANNEL_ID', script)
        self.assertIn('"P_OPS_REVIEW_USER_IDS", "value": CAPTURE_TEST_USER_ID', script)
        self.assertIn('"P_OPS_REVIEW_POSTING_ENABLED", "value": "true" if slack_review_test else "false"', script)
        self.assertIn('"P_OPS_REVIEW_INTERACTIVE_ENABLED", "value": "true" if slack_review_test else "false"', script)
        self.assertIn('"P_OPS_NOTION_WRITE"', script)
        self.assertIn('"P_OPS_LINEAR_WRITE"', script)
        self.assertIn('"P_OPS_SEND_MESSAGES"', script)

    def test_task_progression_pilot_is_allowlisted_and_preparation_only(self):
        script = (ROOT / "scripts" / "deploy_partner_ops_aws.py").read_text()
        self.assertIn('"--enable-task-progression-test"', script)
        self.assertIn("Task progression requires the Slack review test stage.", script)
        self.assertIn('"P_OPS_TASK_PROGRESSION_ENABLED", "value": "true" if task_progression_test else "false"', script)
        self.assertIn('"channel_id": "C0CE711BW06"', script)
        self.assertIn('"P_OPS_SEND_MESSAGES"', script)

    def test_setup_requires_separate_slack_credentials(self):
        guide = (ROOT / "PARTNER-OPS-AWS-SETUP.md").read_text()
        self.assertIn("Do not copy the Team Partner Agent token", guide)
        self.assertIn("capture_enabled=false", guide)
        self.assertIn("working_task_brief_enabled=false", guide)


if __name__ == "__main__":
    unittest.main()
