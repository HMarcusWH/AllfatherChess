"""Recorded search restrictions and numeric costs must match the frozen campaign."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.local_game.test_contracts import events
from tools.local_game.common import QualificationError, file_record, save, sha, source_identity
from tools.local_game.integrity import (
    finite_metrics,
    verify_fastchess_attestation,
    verify_local1_g3_prerequisite_report,
    verify_session_commands,
    verify_specialist_settlements,
)
from tools.local_game.validate import apply_scope_flags, verify_game_clocks, verify_runner_log


class G3PrerequisiteSeparationTests(unittest.TestCase):
    def report(self):
        return {
            "qualification_mode": "local1-mechanism",
            "evidence_valid": True,
            "mechanism_valid": True,
            "source_commit": source_identity(ROOT)["commit"],
            "policy_sha256": sha(ROOT / "qualification/online-hybrid-authority.json"),
            "runtime_config_sha256": sha(ROOT / "config/allfather.online-hybrid.validation.json"),
            "online2_config_sha256": sha(ROOT / "config/allfather.online.cpu-reference.json"),
            "resource_calibration_sha256": sha(
                ROOT / "qualification/online-hybrid-v1-resource-calibration.json"
            ),
            "positive_witness_observed": False,
            "positive_case": None,
            "cases": [
                {
                    "case": "startpos",
                    "run_id": "run-1",
                    "authority": "ANCHOR_FALLBACK",
                    "resource_qualified": True,
                    "route_resource_qualified": True,
                    "clock_outcome": {"output_within_deadline": True},
                    "anchor_move": "e2e4",
                    "emitted_move": "e2e4",
                }
            ],
        }

    def test_local1_accepts_valid_mechanism_evidence_without_positive_override(self):
        verify_local1_g3_prerequisite_report(self.report(), source_identity(ROOT))

    def test_local1_mechanism_report_cannot_launder_identity_or_resource_failure(self):
        for mutation in (
            "mode",
            "source",
            "calibration",
            "resource",
            "deadline",
            "positive-marker",
        ):
            report = self.report()
            if mutation == "mode":
                report["qualification_mode"] = "positive-witness"
            elif mutation == "source":
                report["source_commit"] = "0" * 40
            elif mutation == "calibration":
                report["resource_calibration_sha256"] = "0" * 64
            elif mutation == "resource":
                report["cases"][0]["resource_qualified"] = False
            elif mutation == "deadline":
                report["cases"][0]["clock_outcome"]["output_within_deadline"] = False
            elif mutation == "positive-marker":
                report["positive_witness_observed"] = True
            with self.subTest(mutation=mutation):
                with self.assertRaises(QualificationError):
                    verify_local1_g3_prerequisite_report(
                        report,
                        source_identity(ROOT),
                    )


class EffectiveCommandTests(unittest.TestCase):
    def test_effective_commands_cannot_sneak_a_baseline_work_limit(self):
        plan = {"clock": "0:30+1", "driver_nodes": None}
        lines = events([("in", "setoption name UCI_Chess960 value false"),
                        ("in", "ucinewgame"),
                        ("in", "position startpos"),
                        ("in", "go wtime 31000 btime 31000 winc 1000 binc 1000")])
        verify_session_commands(lines, "allfather-g3", plan, {})
        for suffix in (" nodes 1", " movetime 500", " searchmoves e2e4"):
            changed = copy.deepcopy(lines)
            changed[-1]["line"] += suffix
            with self.assertRaises(QualificationError):
                verify_session_commands(changed, "allfather-g3", plan, {})
        changed = copy.deepcopy(lines)
        changed[-1]["line"] = changed[-1]["line"].replace("binc 1000", "binc 2000")
        with self.assertRaises(QualificationError):
            verify_session_commands(changed, "allfather-g3", plan, {})


    def test_known_scoreless_wrapper_warning_is_scoped_but_other_warnings_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.log"
            path.write_text(
                "Warning; No info line available to extract score from engine allfather-g3\n",
                encoding="utf-8",
            )
            verify_runner_log(path, {"id": "known"})
            path.write_text("Warning; unexpected thing\n", encoding="utf-8")
            with self.assertRaises(QualificationError):
                verify_runner_log(path, {"id": "bad"})

    def test_partial_soak_shard_cannot_claim_full_campaign(self):
        report = {"claim_boundary": {"full_game_lifecycle": False}}
        apply_scope_flags(
            report,
            mode="soak",
            shard={"index": 4, "count": 10},
            errors=[],
        )
        self.assertTrue(report["passed"])
        self.assertTrue(report["shard_passed"])
        self.assertEqual(report["execution_scope"], "partial_soak_shard")
        self.assertFalse(report["baseline_is_complete"])
        self.assertFalse(report["aggregate_soak_complete"])
        self.assertFalse(report["claim_boundary"]["full_game_lifecycle"])

    def test_nonfinite_or_missing_measurement_cannot_be_presented_as_cost(self):
        for value in (None, float("nan"), float("inf"), -1, True):
            with self.assertRaises(QualificationError):
                finite_metrics({"search_metrics": [{"cpu_ms_observed": value}],
                                "resources": {"cpu_complete": True,
                                              "reaped_subtree_cpu_ms": 1,
                                              "proxy_cpu_ms": 1}})
        with self.assertRaises(QualificationError):
            finite_metrics({"search_metrics": [{"cpu_ms_observed": 1}],
                            "resources": {"cpu_complete": False,
                                          "reaped_subtree_cpu_ms": 1,
                                          "proxy_cpu_ms": 1}})

    def test_fastchess_clock_state_is_exactly_reconstructed_from_retained_timeleft(self):
        import chess
        import chess.pgn
        game = chess.pgn.Game(); game.headers["White"]="stockfish"; game.headers["Black"]="reckless"
        n1=game.add_variation(chess.Move.from_uci("e2e4")); n1.comment="0.100s tl=31.900s"
        n2=n1.add_variation(chess.Move.from_uci("e7e5")); n2.comment="0.200s tl=31.800s"
        n3=n2.add_variation(chess.Move.from_uci("g1f3")); n3.comment="0.400s tl=32.500s"
        n4=n3.add_variation(chess.Move.from_uci("b8c6")); n4.comment="0.200s tl=32.600s"
        streams={"stockfish":[{"command":"go wtime 31000 btime 31000 winc 1000 binc 1000"},{"command":"go wtime 31900 btime 31800 winc 1000 binc 1000"}],"reckless":[{"command":"go wtime 31900 btime 31000 winc 1000 binc 1000"},{"command":"go wtime 32500 btime 31800 winc 1000 binc 1000"}]}
        plan={"clock":"0:30+1","driver_nodes":None}; verify_game_clocks(game,streams,plan,0)
        bad=copy.deepcopy(streams); bad["reckless"][0]["command"]="go wtime 1000 btime 31000 winc 1000 binc 1000"
        with self.assertRaises(QualificationError): verify_game_clocks(game,bad,plan,0)
        n1.comment="0.100s tl=1.000s"
        with self.assertRaises(QualificationError): verify_game_clocks(game,streams,plan,0)
        for malformed in ("0.100s","0.100s tl=31.90s","0.100s tl=31.900s tl=31.900s"):
            n1.comment=malformed
            with self.assertRaises(QualificationError): verify_game_clocks(game,streams,plan,0)

    def test_scoreless_allfather_zero_timeleft_uses_elapsed_clock_reconstruction(self):
        import chess
        import chess.pgn
        game=chess.pgn.Game(); game.headers["White"]="allfather-g3"; game.headers["Black"]="stockfish"
        n1=game.add_variation(chess.Move.from_uci("e2e4")); n1.comment="/0 1.796s, tl=0.000s"
        n2=n1.add_variation(chess.Move.from_uci("e7e5")); n2.comment="+0.10/8 0.202s, tl=31.798s"
        streams={
            "allfather-g3":[{"command":"go wtime 31000 btime 31000 winc 1000 binc 1000"}],
            "stockfish":[{"command":"go wtime 30204 btime 31000 winc 1000 binc 1000"}],
        }
        verify_game_clocks(game,streams,{"clock":"0:30+1","driver_nodes":None},0)

        # Zero tl= is a narrowly-scoped Fastchess scoreless-wrapper sentinel,
        # never a generic excuse for missing clock evidence.
        game.headers["White"]="stockfish"; game.headers["Black"]="reckless"
        with self.assertRaises(QualificationError):
            verify_game_clocks(
                game,
                {
                    "stockfish":[{"command":"go wtime 31000 btime 31000 winc 1000 binc 1000"}],
                    "reckless":[{"command":"go wtime 30204 btime 31000 winc 1000 binc 1000"}],
                },
                {"clock":"0:30+1","driver_nodes":None},
                0,
            )

    def test_fastchess_clock_10_plus_1_starts_at_11000(self):
        import chess
        import chess.pgn
        game=chess.pgn.Game(); game.headers["White"]="stockfish"; game.headers["Black"]="reckless"
        n=game.add_variation(chess.Move.from_uci("e2e4")); n.comment="0.100s tl=11.900s"
        verify_game_clocks(game,{"stockfish":[{"command":"go wtime 11000 btime 11000 winc 1000 binc 1000"}],"reckless":[]},{"clock":"0:10+1","driver_nodes":None},0)

    def test_opening_book_plies_leave_fastchess_clock_state_untouched(self):
        import chess
        import chess.pgn
        game=chess.pgn.Game(); game.headers["White"]="stockfish"; game.headers["Black"]="reckless"
        book=game.add_variation(chess.Move.from_uci("e2e4")); searched=book.add_variation(chess.Move.from_uci("e7e5")); searched.comment="0.200s tl=31.800s"
        verify_game_clocks(game,{"stockfish":[],"reckless":[{"command":"go wtime 31000 btime 31000 winc 1000 binc 1000"}]},{"clock":"0:30+1","driver_nodes":None},1)

    def test_specialist_authorize_must_resolve_exactly_once(self):
        authorize = {
            "event": "authorize", "reservation_token": "verify:1:r",
            "granted": True, "phase": "verify",
            "requested_cpu_ms": 10, "requested_gpu_ms": 0,
        }
        settle = {
            "event": "settle", "reservation_token": "verify:1:r",
            "granted": True, "phase": "verify",
            "requested_cpu_ms": 10, "requested_gpu_ms": 0,
        }
        release={**settle,"event":"release"}
        denied={**authorize,"granted":False,"reservation_token":None}
        self.assertTrue(verify_specialist_settlements([authorize,settle],0))
        self.assertTrue(verify_specialist_settlements([denied],0))
        self.assertFalse(verify_specialist_settlements([authorize],1))
        with self.assertRaises(QualificationError): verify_specialist_settlements([authorize,settle,settle],0)
        with self.assertRaises(QualificationError): verify_specialist_settlements([authorize,settle,release],0)
        with self.assertRaises(QualificationError): verify_specialist_settlements([settle],0)
        with self.assertRaises(QualificationError): verify_specialist_settlements([{**denied,"reservation_token":"denied-token"}],0)
        with self.assertRaises(QualificationError): verify_specialist_settlements([{**authorize,"granted":1}],0)

    def test_fastchess_attestation_is_mandatory_and_host_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fc_root = root / "fastchess"
            fc_root.mkdir()
            lock_path = root / "lock.json"
            lock = {
                "repository": "https://example.invalid/fastchess.git",
                "commit": "a" * 40,
                "tree": "b" * 40,
            }
            save(lock_path, lock)
            attestation_path = fc_root / "source-test-attestation.json"
            attestation = {
                "passed": True,
                "repository": lock["repository"],
                "commit": lock["commit"],
                "tree": lock["tree"],
                "lock_sha256": sha(lock_path),
                "reference_host": "ubuntu-22.04",
                "os_release": {"ID": "ubuntu", "VERSION_ID": "22.04"},
                "contract": "clean -> make tests -> fastchess-tests",
                "compiler": "g++ test",
            }
            save(attestation_path, attestation)
            fc = {
                "upstream_tests_passed": True,
                "lock_sha256": sha(lock_path),
                "source_test_attestation": file_record(attestation_path, fc_root),
            }
            verify_fastchess_attestation(fc_root, fc, lock_path)
            missing = dict(fc)
            missing.pop("source_test_attestation")
            with self.assertRaises(QualificationError):
                verify_fastchess_attestation(fc_root, missing, lock_path)
            attestation["reference_host"] = "ubuntu-24.04"
            save(attestation_path, attestation)
            fc["source_test_attestation"] = file_record(attestation_path, fc_root)
            with self.assertRaises(QualificationError):
                verify_fastchess_attestation(fc_root, fc, lock_path)
            attestation["reference_host"]="ubuntu-22.04"; attestation["tree"]="c"*40
            save(attestation_path,attestation); fc["source_test_attestation"]=file_record(attestation_path,fc_root)
            with self.assertRaises(QualificationError): verify_fastchess_attestation(fc_root,fc,lock_path)
            attestation["tree"]=lock["tree"]; attestation["lock_sha256"]="0"*64
            save(attestation_path,attestation); fc["source_test_attestation"]=file_record(attestation_path,fc_root)
            with self.assertRaises(QualificationError): verify_fastchess_attestation(fc_root,fc,lock_path)


if __name__ == "__main__":
    unittest.main()
