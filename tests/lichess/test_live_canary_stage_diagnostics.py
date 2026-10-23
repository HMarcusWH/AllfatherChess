"""Strict, non-secret stage diagnostics without bypassing frozen offline staging."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from deploy.lichess import live_session
from scripts.sanitize_live_canary_preflight import sanitize


class StageBoundaryTests(unittest.TestCase):
    def test_pinned_source_failure_denies_staging(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with mock.patch.object(live_session,"_pinned_upstream_precheck",
                                   side_effect=ValueError("Bearer synthetic-private")), \
                 mock.patch.object(live_session,"_sealed_engine_precheck") as seal, \
                 mock.patch.object(live_session.offline_session,"stage") as offline:
                with self.assertRaises(live_session.LiveStageError) as caught:
                    live_session.stage_live(release=root/"release",
                                            bridge=root/"bridge",
                                            workspace=root/"session",
                                            opponent="approved_test_bot")
                self.assertEqual(caught.exception.code,"UPSTREAM_LOCK_CHECK")
                self.assertNotIn("Bearer",str(caught.exception))
                seal.assert_not_called()
                offline.assert_not_called()

    def test_engine_refusal_preserves_exact_stage(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with mock.patch.object(live_session,"_pinned_upstream_precheck"), \
                 mock.patch.object(live_session,"_sealed_engine_precheck",
                                   side_effect=ValueError("secret-should-not-leak")), \
                 mock.patch.object(live_session.offline_session,"stage") as offline:
                with self.assertRaises(live_session.LiveStageError) as caught:
                    live_session.stage_live(release=root/"release",
                                            bridge=root/"bridge",
                                            workspace=root/"session",
                                            opponent="approved_test_bot")
                self.assertEqual(caught.exception.code,"ENGINE_SEAL_CHECK")
                self.assertNotIn("secret",str(caught.exception))
                offline.assert_not_called()

    def test_original_frozen_offline_staging_is_always_invoked(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with mock.patch.object(live_session,"_pinned_upstream_precheck"), \
                 mock.patch.object(live_session,"_sealed_engine_precheck"), \
                 mock.patch.object(live_session.offline_session,"stage",
                                   side_effect=OSError("fixture raw upstream detail")) as offline:
                with self.assertRaises(live_session.LiveStageError) as caught:
                    live_session.stage_live(release=root/"release",
                                            bridge=root/"bridge",
                                            workspace=root/"session",
                                            opponent="approved_test_bot")
                self.assertEqual(caught.exception.code,"OFFLINE_STAGE_COPY_OR_CONFIG")
                offline.assert_called_once()
                self.assertNotIn("upstream detail",str(caught.exception))

    def test_allowlisted_negative_receipt_cannot_persist_tokens(self):
        raw='Bearer secret=abc\\n'+json.dumps({
            "scope":"synthetic-stage-preflight-only","status":"FAIL",
            "stage_code":"ENGINE_SEAL_CHECK",
            "exception_type":"CalledProcessError",
            "environment":"Bearer-private-is-not-exported"})
        verdict=sanitize(raw,2)
        self.assertEqual(verdict["stage_code"],"ENGINE_SEAL_CHECK")
        self.assertEqual(verdict["exception_type"],"CalledProcessError")
        self.assertFalse(verdict["qualifies_dry_run"])
        self.assertNotIn("Bearer",str(verdict))
        self.assertNotIn("private",str(verdict))
        self.assertIsNone(sanitize(raw,0))

    def test_unknown_stage_cannot_be_injected(self):
        raw=json.dumps({"scope":"synthetic-stage-preflight-only","status":"FAIL",
                        "stage_code":"TOKEN_PRIVATE_VALUE","exception_type":"SecretClass"})
        verdict=sanitize(raw,124)
        self.assertEqual(verdict["stage_code"],"STAGE_FAILURE_UNCLASSIFIED")
        self.assertEqual(verdict["exception_type"],"OtherError")
        self.assertNotIn("TOKEN_PRIVATE_VALUE",str(verdict))


if __name__=="__main__":
    unittest.main()
