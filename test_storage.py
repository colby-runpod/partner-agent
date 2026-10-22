import json
import os
import tempfile
import unittest
from unittest.mock import patch
import storage


class StorageConfigurationTests(unittest.TestCase):
    def test_invalid_backend_stops(self):
        with patch.dict(os.environ,STORAGE_BACKEND='postgre'):
            with self.assertRaises(ValueError): storage.make_store(None)

    def test_postgres_requires_explicit_state_readiness(self):
        with patch.dict(os.environ,{'STORAGE_BACKEND':'postgres'},clear=True):
            with self.assertRaisesRegex(ValueError,'reconciled state'): storage.make_store(None)

    def test_only_runtime_identity_accepted_without_leaking_password(self):
        with tempfile.NamedTemporaryFile() as ca:
            settings=dict(POSTGRES_STATE_READY='true',DB_HOST='fixture',SLACK_TEAM_ID='T1',
                          DB_SSL_ROOT_CERT=ca.name,RUNTIME_CREDENTIAL=json.dumps({'username':'agent_admin','password':'sensitive-fixture'}))
            with patch.dict(os.environ,settings,clear=True):
                with self.assertRaises(ValueError) as caught: storage.postgres_settings()
                self.assertNotIn('sensitive-fixture',str(caught.exception))

    def test_missing_ca_rejected(self):
        with patch.dict(os.environ,dict(POSTGRES_STATE_READY='true',DB_HOST='fixture',SLACK_TEAM_ID='T1',
                                      DB_SSL_ROOT_CERT='/does-not-exist'),clear=True):
            with self.assertRaises(ValueError): storage.postgres_settings()

    def test_standby_does_not_initialize_database(self):
        import service
        with patch.dict(os.environ,{'AGENT_RUNTIME_MODE':'standby','STORAGE_BACKEND':'postgres'},clear=True):
            with patch('standby.serve') as serve, patch('storage.make_store') as make:
                service.main()
                serve.assert_called_once()
                make.assert_not_called()
