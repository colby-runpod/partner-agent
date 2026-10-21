import unittest
from unittest.mock import patch
import service


class StandbyTests(unittest.TestCase):
    def test_standby_does_not_start_any_workers(self):
        with patch.dict('os.environ', {'AGENT_RUNTIME_MODE': 'standby', 'OUTBOX_ENABLED': 'true'}), patch('standby.serve') as serve, patch('subprocess.Popen') as spawn:
            service.main()
            serve.assert_called_once()
            spawn.assert_not_called()

    def test_invalid_mode_fails_closed(self):
        with patch.dict('os.environ', {'AGENT_RUNTIME_MODE': 'standy'}), patch('subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):
                service.main()
            spawn.assert_not_called()
