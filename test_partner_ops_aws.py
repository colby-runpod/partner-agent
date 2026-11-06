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

    def test_setup_requires_separate_slack_credentials(self):
        guide = (ROOT / "PARTNER-OPS-AWS-SETUP.md").read_text()
        self.assertIn("Do not copy the Team Partner Agent token", guide)
        self.assertIn("capture_enabled=false", guide)
        self.assertIn("working_task_brief_enabled=false", guide)


if __name__ == "__main__":
    unittest.main()
