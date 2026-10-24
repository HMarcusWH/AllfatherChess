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
        raw='Bearer secret=abc\n'+json.dumps({
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


    def test_frozen_offline_operation_isolated_without_exposing_exception(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(live_session, "_pinned_upstream_precheck"), \
                 mock.patch.object(live_session, "_sealed_engine_precheck"), \
                 mock.patch.object(live_session.offline_session.fetch_bridge, "verify"), \
                 mock.patch.object(live_session.offline_session.subprocess, "run",
                     return_value=SimpleNamespace(returncode=0,
                         stdout="offline seal verified", stderr="")), \
                 mock.patch.object(live_session.offline_session.shutil, "copytree",
                     side_effect=live_session.shutil.Error("Bearer private-token")):
                with self.assertRaises(live_session.LiveStageError) as caught:
                    live_session.stage_live(
                        release=root/"release", bridge=root/"bridge",
                        workspace=root/"session", opponent="approved_test_bot")
                self.assertEqual(caught.exception.code, "OFFLINE_STAGE_COPY_OR_CONFIG")
                self.assertEqual(caught.exception.operation_code, "OFFLINE_STAGE_RELEASE_COPY")
                self.assertNotIn("Bearer", str(caught.exception))
                receipt=sanitize(json.dumps({
                    "scope":"synthetic-stage-preflight-only",
                    "status":"FAIL",
                    "stage_code":caught.exception.code,
                    "exception_type":"ShutilError",
                    "offline_operation":caught.exception.operation_code,
                    "secret":"Bearer private-token",
                }), 2)
                self.assertEqual(receipt["offline_operation"], "OFFLINE_STAGE_RELEASE_COPY")
                self.assertEqual(receipt["exception_type"], "ShutilError")
                self.assertNotIn("Bearer", str(receipt))

    def test_unknown_operation_detail_cannot_be_injected(self):
        payload=json.dumps({
            "scope":"synthetic-stage-preflight-only","status":"FAIL",
            "stage_code":"OFFLINE_STAGE_COPY_OR_CONFIG",
            "exception_type":"AttributeError",
            "offline_operation":"Bearer stolen-secret",
        })
        verdict=sanitize(payload, 2)
        self.assertEqual(verdict["offline_operation"], "NOT_APPLICABLE")
        self.assertNotIn("stolen-secret", str(verdict))

    def test_non_offline_stage_must_not_accept_offline_operation(self):
        payload=json.dumps({
            "scope":"synthetic-stage-preflight-only","status":"FAIL",
            "stage_code":"UPSTREAM_LOCK_CHECK",
            "exception_type":"ShutilError",
            "offline_operation":"OFFLINE_STAGE_RELEASE_COPY",
        })
        self.assertEqual(sanitize(payload, 2)["offline_operation"], "NOT_APPLICABLE")


    def test_actual_config_source_classifies_generic_upstream_exception(self):
        import sys
        import types
        import yaml
        from deploy.lichess import bridge_policy
        # This is a synthetic validator stub. The original pinned upstream
        # module is not replaced or modified in production.
        config_module=types.ModuleType("lib.config")
        config_module.insert_default_values=lambda cfg: None
        def generic_failure(_cfg):
            raise Exception("Bearer fixture token must never be serialized")
        config_module.validate_config=generic_failure
        upstream=types.ModuleType("lib")
        upstream.__path__=[]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            bridge=root/"pinned-bridge"
            bridge.mkdir()
            release=root/"release"
            launcher=release/"deploy/bin/allfather-online"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("fixture")
            data={
                "engine":{
                    "polyglot":{"enabled":False},
                    "online_moves":{name:{"enabled":False} for name in
                        ("chessdb_book","lichess_cloud_analysis",
                         "lichess_opening_explorer","online_egtb")},
                    "lichess_bot_tbs":{
                        "syzygy":{"enabled":False},"gaviota":{"enabled":False}},
                    "draw_or_resign":{
                        "resign_enabled":False,"offer_draw_enabled":False}},
                "challenge":{},"greeting":{},"matchmaking":{},
            }
            (bridge/"config.yml.default").write_text(yaml.safe_dump(data))
            with mock.patch.dict(sys.modules,{"lib":upstream,"lib.config":config_module}):
                try:
                    bridge_policy.generate(
                        bridge,release,"http://127.0.0.1:17832/",root/"effective.yml")
                except Exception as exc:
                    self.assertEqual(type(exc).__name__,"Exception")
                    self.assertEqual(
                        live_session._frozen_config_substage(exc),
                        "UPSTREAM_VALIDATE")
                    self.assertNotIn("Bearer",str(live_session.LiveStageError(
                        "OFFLINE_STAGE_COPY_OR_CONFIG",
                        operation_code="OFFLINE_STAGE_CONFIG_GENERATE",
                        config_substage="UPSTREAM_VALIDATE")))
                else:
                    self.fail("synthetic upstream generic Exception did not fire")

    def test_config_substage_evidence_is_allowlisted_and_scope_bound(self):
        record={
            "scope":"synthetic-stage-preflight-only","status":"FAIL",
            "stage_code":"OFFLINE_STAGE_COPY_OR_CONFIG",
            "offline_operation":"OFFLINE_STAGE_CONFIG_GENERATE",
            "exception_type":"Exception",
            "config_substage":"UPSTREAM_VALIDATE",
            "untrusted_message":"Bearer synthetic-sensitive-value"
        }
        result=sanitize(json.dumps(record),2)
        self.assertEqual(result["config_substage"],"UPSTREAM_VALIDATE")
        self.assertEqual(result["exception_type"],"Exception")
        self.assertNotIn("Bearer",str(result))
        for key,value in (
            ("config_substage","PRIVATE_PATH_OR_BEARER"),
            ("offline_operation","OFFLINE_STAGE_BRIDGE_EXPORT"),
            ("stage_code","EXEC_STAGE_MOUNT_CHECK")):
            modified={**record,key:value}
            self.assertEqual(sanitize(json.dumps(modified),2)["config_substage"],
                             "NOT_APPLICABLE")

    def test_unknown_stage_cannot_be_injected(self):
        raw=json.dumps({"scope":"synthetic-stage-preflight-only","status":"FAIL",
                        "stage_code":"TOKEN_PRIVATE_VALUE","exception_type":"SecretClass"})
        verdict=sanitize(raw,124)
        self.assertEqual(verdict["stage_code"],"STAGE_FAILURE_UNCLASSIFIED")
        self.assertEqual(verdict["exception_type"],"OtherError")
        self.assertNotIn("TOKEN_PRIVATE_VALUE",str(verdict))


if __name__=="__main__":
    unittest.main()
