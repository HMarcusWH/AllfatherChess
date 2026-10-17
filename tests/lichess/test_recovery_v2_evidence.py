"""Independent OFFLINE-4B evidence checks: actual SQLite bytes, not producer flags."""
from __future__ import annotations
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from deploy.lichess.session_owner import SessionOwner

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "online4b_qualifier", ROOT / "scripts/qualify-online-recovery-v2.py")
qual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qual)


class RecoveryV2EvidenceTests(unittest.TestCase):
    def test_http_target_policy_is_in_exact_guarded_source_closure(self):
        exact_sha="b"*40
        names=json.loads((ROOT/"qualification/online-recovery-v2.json")
                         .read_text())["attested_paths"]
        path="tests/lichess/http_target_contract.py"
        self.assertEqual(names.count(path),1)
        file_hashes={name:qual.hash_file(ROOT/name) for name in names}
        document={"source_commit":exact_sha,"files":file_hashes}
        qual.attest(document,exact_sha,names)
        forged=copy.deepcopy(document)
        forged["files"][path]="0"*64
        with self.assertRaisesRegex(ValueError,"source hash mismatch"):
            qual.attest(forged,exact_sha,names)

    def test_persisted_journal_verifies_and_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            with SessionOwner(folder / "state") as owner:
                owner.journal.observe(owner.epoch, "off3b001",
                                      {"moves": "", "status": "started"})
                owner.journal.prepare(owner.epoch, "off3b001", (), "e2e4")
                record = owner.journal.snapshot()
            qual.verify_persisted_sqlite(folder, record)
            altered = copy.deepcopy(record)
            altered["intents"][0]["status"] = "CONFIRMED"
            with self.assertRaisesRegex(ValueError, "differs from runner"):
                qual.verify_persisted_sqlite(folder, altered)
            altered = copy.deepcopy(record)
            altered["owner_epoch"] = "fake"
            with self.assertRaisesRegex(ValueError, "owner differs"):
                qual.verify_persisted_sqlite(folder, altered)

    def test_absent_persisted_database_never_qualifies(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "absent"):
                qual.verify_persisted_sqlite(Path(d), {"owner_epoch": "fake"})

    def test_r13_uses_real_process_exit_not_an_assumed_success(self):
        valid = {"observation": "injected_bridge_kill", "bridge_exit": -9,
                 "kill_issued": True, "kill_signal": 9, "kill_monotonic_ns": 321321}
        self.assertEqual(qual.r13_witness([valid])["bridge_exit"], -9)
        for forged in (
            {"observation": "injected_bridge_kill", "bridge_exit": None},
            {"observation": "injected_bridge_kill", "bridge_exit": 0},
            {"observation": "not_killed", "bridge_exit": -9},
            {**valid, "kill_issued": False},
            {**valid, "kill_signal": None},
            {**valid, "kill_monotonic_ns": 0},
            {},
        ):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                qual.r13_witness([forged])

    def test_normal_completion_is_never_called_suspension(self):
        got = qual.classify_disposition(
            scenario_id="R00", game_complete=True, recovered=False,
            explicit_suspension=False, restart_attempted=False,
        )
        self.assertEqual(got, "NORMAL_COMPLETION")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R00", game_complete=False, recovered=False,
            explicit_suspension=False, restart_attempted=False,
        ), "INCONCLUSIVE")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R02", game_complete=True, recovered=False,
            explicit_suspension=False, restart_attempted=True,
        ), "INCONCLUSIVE")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R10", game_complete=False, recovered=False,
            explicit_suspension=True, restart_attempted=False,
        ), "SAFE_SUSPENSION")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R08", game_complete=False, recovered=False,
            explicit_suspension=False, restart_attempted=True,
        ), "INCONCLUSIVE")

    def test_terminal_journal_must_confirm_every_server_move(self):
        game = {
            "game_id": "off3b001", "complete": True,
            "moves": ["e2e4"], "accepted": [{"ply": 1, "uci": "e2e4"}],
        }
        journal = {
            "games": [{"game_id": "off3b001", "terminal": 1,
                       "history_json": '["e2e4"]'}],
            "intents": [{"game_id": "off3b001", "ply_before": 0,
                         "move": "e2e4", "status": "CONFIRMED"}],
        }
        phase = {
            "observation": "server_game_complete",
            "terminal_sync": {
                "complete": True, "server_final_ply": 1, "journal_ply": 1,
                "journal_terminal": True, "history_matches": True,
                "unconfirmed_accepted_moves": [],
            },
        }
        self.assertTrue(qual.verify_terminal_confirmation(game, journal, [phase]))
        for mutate in (
            lambda j, p: j["intents"][0].update(status="ACK_OBSERVED"),
            lambda j, p: j["games"][0].update(terminal=0),
            lambda j, p: p["terminal_sync"].update(complete=False),
            lambda j, p: p["terminal_sync"].update(journal_ply=0),
            lambda j, p: j["games"][0].update(history_json='[]'),
        ):
            bad_j, bad_p = copy.deepcopy(journal), copy.deepcopy(phase)
            mutate(bad_j, bad_p)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                qual.verify_terminal_confirmation(game, bad_j, [bad_p])

    def test_restart_recovery_requires_new_epoch_move_and_server_commit(self):
        first, second = "a"*32, "b"*32
        game_id, uci, ply = "off3b001", "d2d4", 2
        phases = [
            {"epoch": first, "observation": "restart_requested_after_fault"},
            {"epoch": second, "old_descendants_drained": True,
             "observation": "server_game_complete"},
        ]
        events = [
            {"event_id": 1, "epoch": second,
             "event": "owner_fenced_and_activated", "game_id": None, "details": "{}"},
            {"event_id": 2, "epoch": second,
             "event": "move_prepared", "game_id": game_id,
             "details": '{"ply_before":2,"move":"d2d4"}'},
            {"event_id": 3, "epoch": second,
             "event": "intent_submitting", "game_id": game_id,
             "details": '{"ply_before":2,"move":"d2d4"}'},
            {"event_id": 4, "epoch": second,
             "event": "server_confirmed", "game_id": game_id,
             "details": '{"ply_before":2,"move":"d2d4"}'},
        ]
        journal = {
            "owner_epoch": second, "events": events,
            "intents": [{"game_id": game_id, "ply_before": ply,
                         "move": uci, "epoch": second, "status": "CONFIRMED"}],
        }
        trace = [
            {"seq": 5, "event": "move_attempt", "game_id": game_id, "uci": uci},
            {"seq": 6, "event": "board_commit", "game_id": game_id,
             "ply_before": ply, "uci": uci, "origin": "allfather",
             "request_id": "request-5"},
        ]
        guarded = [
            {"event": "authoritative_snapshot_observed", "owner_epoch": second,
             "game_id": game_id, "ply": ply},
            {"event": "http_move_submission_attempt", "owner_epoch": second,
             "game_id": game_id, "ply": ply, "uci": uci},
        ]
        self.assertTrue(qual.verify_fresh_epoch_recovery(phases, journal, trace, guarded))
        mutations = (
            (phases, {**journal, "intents": [
                {**journal["intents"][0], "epoch": first}]}, trace, guarded),
            (phases, journal, trace[:1], guarded),
            (phases, journal, trace, guarded[:1]),
            (phases, {**journal, "events": events[:3]}, trace, guarded),
            (phases, journal, trace,
             [{**guarded[0], "owner_epoch": first}, guarded[1]]),
        )
        for values in mutations:
            with self.subTest(values=values):
                self.assertFalse(qual.verify_fresh_epoch_recovery(*values))
        invalid_phase = [phases[0], {**phases[1], "old_descendants_drained": False}]
        with self.assertRaises(ValueError):
            qual.verify_fresh_epoch_recovery(invalid_phase, journal, trace, guarded)

    def test_postgame_barrier_waits_for_real_confirmation(self):
        from deploy.lichess.run_recovery_v2 import (
            terminal_snapshot, wait_for_terminal_sync,
        )
        game = {"complete": True, "game_id": "off3b001",
                "moves": ["e2e4"], "accepted": [{"ply": 1, "uci": "e2e4"}]}
        pending = {"games": [{"game_id": "off3b001",
                              "history_json": "[]", "terminal": 0}],
                   "intents": [{"game_id": "off3b001", "ply_before": 0,
                                "move": "e2e4", "status": "ACK_OBSERVED"}]}
        done = {"games": [{"game_id": "off3b001",
                           "history_json": '["e2e4"]', "terminal": 1}],
                "intents": [{"game_id": "off3b001", "ply_before": 0,
                             "move": "e2e4", "status": "CONFIRMED"}]}
        class Journal:
            calls = 0
            def snapshot(self):
                self.calls += 1
                return pending if self.calls < 2 else done
        class Server:
            def evidence(self):
                return game
        self.assertFalse(terminal_snapshot(type("J", (), {"snapshot": lambda self: pending})(), game)["complete"])
        observed = wait_for_terminal_sync(Journal(), Server(), timeout_s=0.5)
        self.assertTrue(observed["complete"])
        self.assertEqual(observed["server_final_ply"], 1)
        self.assertEqual(observed["journal_ply"], 1)
        self.assertEqual(observed["unconfirmed_accepted_moves"], [])
        always_pending = type("J", (), {"snapshot": lambda self: pending})()
        timed_out = wait_for_terminal_sync(always_pending, Server(), timeout_s=0.01)
        self.assertFalse(timed_out["complete"])

    def test_unresolved_restart_cannot_be_mislabeled_as_recovered(self):
        self.assertEqual(qual.classify_disposition(
            scenario_id="R13", game_complete=True, recovered=False,
            explicit_suspension=False, restart_attempted=True,
        ), "INCONCLUSIVE")


    def test_valid_diagnostic_claims_do_not_authorize_online_release(self):
        value = qual.read(qual.POLICY)
        self.assertEqual(value["scope"], "offline-guarded-bridge-only")
        self.assertFalse(value["network_recovery_qualified"])
        self.assertFalse(value["public_bot_release"])
        self.assertFalse(value["deployment_host_qualified"])


    def test_guarded_request_ledger_rejects_duplicate_illegal_and_forbidden(self):
        gid = "off3b001"
        path = f"/api/bot/game/{gid}/move/e2e4"
        game = {"game_id": gid, "color": "white", "moves": ["e2e4"],
                "forbidden_attempts": []}
        requests = [{"method": "POST", "path": path,
                     "request_target": path, "target_canonical": True,
                     "status": 200, "request_id": "request-1"}]
        trace = [
            {"seq":1,"event":"move_attempt","game_id":gid,"uci":"e2e4",
             "path":path,"ply_at_receipt":0},
            {"seq":2,"event":"board_commit","game_id":gid,"origin":"allfather",
             "uci":"e2e4","ply_before":0},
        ]
        intents=[{"game_id":gid,"ply_before":0,"move":"e2e4","history_json":"[]"}]
        self.assertEqual(
            qual.verify_guarded_requests(requests,trace,game,intents)["unique_legal_move_attempts"],
            1)
        invalid_cases = (
            (requests + [{"method":"POST","path":path,"status":409}],
             trace + [{"seq":3, "event":"move_attempt","game_id":gid,
                       "uci":"e2e4","path":path,"ply_at_receipt":1}],game,intents),
            ([{"method":"POST","path":f"/api/bot/game/{gid}/move/e7e5","status":400}],
             [{"seq":1,"event":"move_attempt","game_id":gid,"uci":"e7e5",
               "path":f"/api/bot/game/{gid}/move/e7e5","ply_at_receipt":0}],
             {**game,"moves":[]},intents),
            (requests+[{"method":"POST","path":f"/api/bot/game/{gid}/abort","status":403}],
             trace, {**game,"forbidden_attempts":[f"POST /api/bot/game/{gid}/abort"]},intents),
            (requests,trace,game,[{**intents[0],"history_json":'["d2d4"]'}]),
        )
        for mutated in invalid_cases:
            with self.subTest(mutated=mutated),self.assertRaises(ValueError):
                qual.verify_guarded_requests(*mutated)

    def test_guarded_audit_cannot_swap_valid_move_request_ids(self):
        gid="off3b001"
        path=f"/api/bot/game/{gid}/move/e2e4"
        trace=[{"seq":1,"event":"move_attempt","game_id":gid,
                "uci":"e2e4","path":path,"ply_at_receipt":0},
               {"seq":2,"event":"board_commit","game_id":gid,
                "uci":"e2e4","origin":"allfather","ply_before":0}]
        game={"game_id":gid,"color":"white","moves":["e2e4"],
              "forbidden_attempts":[]}
        intents=[{"game_id":gid,"ply_before":0,"move":"e2e4",
                  "history_json":"[]"}]
        for rid in ("request-2", "request-unseen", None):
            with self.subTest(request_id=rid),self.assertRaisesRegex(
                    ValueError,"request ID|request IDs"):
                qual.verify_guarded_requests(
                    [{"method":"POST","path":path,"request_target":path,
                      "target_canonical":True,"request_id":rid,
                      "status":200}],trace,game,intents)

    def test_guarded_raw_get_semantics_cannot_be_forged(self):
        gid="off3b001"
        path=f"/api/bot/game/{gid}/move/e2e4"
        requests=[{"method":"POST","path":path,
                   "request_target":path,"target_canonical":True,
                   "request_id":"request-1","status":200}]
        trace=[{"seq":1,"event":"move_attempt","game_id":gid,
                "uci":"e2e4","path":path,"ply_at_receipt":0},
               {"seq":2,"event":"board_commit","game_id":gid,
                "uci":"e2e4","origin":"allfather","ply_before":0}]
        game={"game_id":gid,"color":"white","moves":["e2e4"],
              "forbidden_attempts":[]}
        intents=[{"game_id":gid,"ply_before":0,"move":"e2e4",
                  "history_json":"[]"}]
        for raw in ("//api/account", "/api/account?access_token=secret",
                    "/api/users/status?ids=other"):
            with self.subTest(raw=raw),self.assertRaisesRegex(
                    ValueError,"guarded HTTP request target"):
                qual.verify_guarded_requests(
                    requests+[{"method":"GET","path":raw.split("?",1)[0].lstrip("/"),
                               "request_target":raw,
                               "target_canonical":False,"status":200}],
                    trace,game,intents)
        # Even a producer-controlled "canonical" bit cannot override a
        # mismatched raw target with the same approved visible pathname.
        with self.assertRaisesRegex(ValueError,"guarded HTTP request target"):
            qual.verify_guarded_requests(
                requests+[{"method":"GET","path":"/api/account",
                           "request_target":"/api/account?access_token=x",
                           "target_canonical":True,"status":200}],
                trace,game,intents)

    def test_legal_but_different_guarded_move_url_is_not_traced_intent(self):
        gid="off3b001"
        path=f"/api/bot/game/{gid}/move/e2e4"
        requests=[{"method":"POST","path":path,"request_target":path,
                   "target_canonical":True,"status":200,
                   "request_id":"request-1"}]
        # d2d4 is independently legal, committed and durably prepared;
        # the actual server URL submitted e2e4 instead.
        trace=[{"seq":1,"event":"move_attempt","game_id":gid,
                "uci":"d2d4","path":path,"ply_at_receipt":0},
               {"seq":2,"event":"board_commit","game_id":gid,
                "uci":"d2d4","origin":"allfather","ply_before":0}]
        game={"game_id":gid,"color":"white","moves":["d2d4"],
              "forbidden_attempts":[]}
        intents=[{"game_id":gid,"ply_before":0,"move":"d2d4",
                  "history_json":"[]"}]
        with self.assertRaisesRegex(ValueError,
                                    "guarded move URL differs from traced"):
            qual.verify_guarded_requests(requests,trace,game,intents)

    def test_encoded_move_url_aliases_are_rejected(self):
        gid="off3b001"
        encoded=f"/api/bot/game/{gid}/move/%65%32e4"
        trace=[{"seq":1,"event":"move_attempt","game_id":gid,
                "uci":"e2e4","path":encoded,"ply_at_receipt":0},
               {"seq":2,"event":"board_commit","game_id":gid,
                "uci":"e2e4","origin":"allfather","ply_before":0}]
        game={"game_id":gid,"color":"white","moves":["e2e4"],
              "forbidden_attempts":[]}
        intents=[{"game_id":gid,"ply_before":0,"move":"e2e4",
                  "history_json":"[]"}]
        with self.assertRaisesRegex(ValueError,"guarded HTTP request target"):
            qual.verify_guarded_requests(
                [{"method":"POST","path":encoded,"request_target":encoded,
                  "target_canonical":True,"status":200,
                  "request_id":"request-1"}],
                trace,game,intents)

    def test_journal_history_must_equal_authoritative_prefix(self):
        from deploy.lichess.reconcile import position_id
        gid, epoch = "off3b001", "a"*32
        game = {"game_id":gid,"moves":["e2e4","e7e5"]}
        journal = {"owner_epoch":epoch,"events":[{"event_id":1,"event":"observe"}],
                   "games":[],
                   "intents":[{"game_id":gid,"ply_before":2,"move":"g1f3",
                               "history_json":'["e2e4", "e7e5"]',
                               "position_id":position_id(("e2e4","e7e5")),
                               "status":"PREPARED"}]}
        result={"phases":[{"epoch":epoch}]}
        guarded=[{"event":"authoritative_snapshot_observed"}]
        qual.verify_journal(journal,[],game,result,guarded)
        wrong=copy.deepcopy(journal)
        wrong["intents"][0]["history_json"]='["d2d4", "d7d5"]'
        wrong["intents"][0]["position_id"]=position_id(("d2d4","d7d5"))
        with self.assertRaisesRegex(ValueError,"authoritative game history"):
            qual.verify_journal(wrong,[],game,result,guarded)

    def test_overlay_verifier_compares_keys_and_digests_not_counts(self):
        expected={"upstream_commit":"a"*40,"patched_upstream":{
                  "lib/lichess.py":{"git_blob_sha1":"b"*40,"patched_sha256":"c"*64}},
                  "guard_modules":{"guarded_transport.py":"d"*64},
                  "mode":"OFFLINE-4B-ONLY","public_bot_release":False}
        qual.verify_overlay_identity(expected,copy.deepcopy(expected))
        mutated=copy.deepcopy(expected)
        mutated["patched_upstream"]["lib/lichess.py"]["patched_sha256"]="e"*64
        with self.assertRaisesRegex(ValueError,"overlay"):
            qual.verify_overlay_identity(mutated,expected)


if __name__ == "__main__":
    unittest.main()
