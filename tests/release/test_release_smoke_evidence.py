"""Adversarial RELEASE-1 evidence fixtures with legal game and HTTP traces.

A valid evidence graph is constructed first; each negative case mutates a
single contract property and recomputes the non-authenticating trace digest.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release_smoke_evidence_qualifier", ROOT / "scripts/qualify-release-smoke.py")
qual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qual)


def fixture(key):
    scenario, color = qual.CASES[key]
    first, second = "a"*32, "b"*32
    board = chess.Board()
    rows, requests, guarded, intents, accepted, commits = [], [], [], [], [], []
    activations = []
    def event(kind, **kw):
        row = {"seq":len(rows)+1, "event":kind,
               "monotonic_ns":(len(rows)+1)*1_000_000, **kw}
        rows.append(row)
        return row
    requests.extend([
        {"method":"POST","path":"/api/token/test","status":200,"monotonic_ms":10},
        {"method":"POST","path":f"/api/challenge/{qual.GAME_ID}/accept",
         "status":200,"monotonic_ms":20},
    ])
    if scenario == "R10":
        uci, ply = "e2e4", 0
        path = qual.MOVE_PREFIX + uci
        request = event("move_attempt",game_id=qual.GAME_ID,
                        ply_at_receipt=ply,uci=uci,path=path)
        rid = f"request-{request['seq']}"
        fault = event("fault_activated",action="rate_limit_429",
                      endpoint="move_post",request_id=rid)
        completed = event("response_write_completed",status=429,path=path,
                          request_id=rid)
        row = event("rate_limit_hold_completed",action="rate_limit_429",
                    request_id=rid)
        row["monotonic_ns"] = completed["monotonic_ns"] + 61_500_000_000
        activations.append({"action":"rate_limit_429","request_id":rid})
        requests.append({"method":"POST","path":path,"status":429,
                         "monotonic_ms":1000,"request_id":rid})
        guarded.append({"event":"http_move_submission_attempt",
                        "owner_epoch":first,"game_id":qual.GAME_ID,
                        "ply":ply,"uci":uci})
        guarded.append({"event":"global_rate_limit_block",
                        "owner_epoch":first,"endpoint":"move","status":429,
                        "timestamp":1800000000.25})
        intents.append({"game_id":qual.GAME_ID,"epoch":first,
                        "ply_before":ply,"move":uci,"history_json":"[]",
                        "status":"UNKNOWN_ACK"})
    else:
        bot_index = 0
        for uci in ("f2f3","e7e5","g2g4","d8h4"):
            ply = len(board.move_stack)
            before = board.fen()
            is_bot = board.turn == (color=="white")
            rid = None
            epoch = second if scenario=="R02" and bot_index else first
            if is_bot:
                path = qual.MOVE_PREFIX + uci
                a = event("move_attempt",game_id=qual.GAME_ID,uci=uci,
                          path=path,ply_at_receipt=ply)
                rid = f"request-{a['seq']}"
                if scenario=="R02" and bot_index==0:
                    event("fault_activated",action="commit_then_close",
                          endpoint="move_post",request_id=rid)
                    activations.append({"action":"commit_then_close",
                                        "request_id":rid})
                requests.append({"method":"POST","path":path,"status":200,
                                 "monotonic_ms":1000+ply*1000,
                                 "request_id":rid})
                intents.append({"game_id":qual.GAME_ID,"epoch":epoch,
                                "ply_before":ply,"move":uci,
                                "history_json":json.dumps(
                                    [m.uci() for m in board.move_stack]),
                                "status":"CONFIRMED"})
                guarded.append({"event":"http_move_submission_attempt",
                                "owner_epoch":epoch,"game_id":qual.GAME_ID,
                                "ply":ply,"uci":uci})
                bot_index += 1
            board.push_uci(uci)
            c = event("board_commit",game_id=qual.GAME_ID,
                      origin="allfather" if is_bot else "scripted_opponent",
                      request_id=rid,uci=uci,ply_before=ply,
                      ply_after=len(board.move_stack),fen_before=before,
                      fen_after=board.fen())
            commits.append(c)
            if is_bot:
                accepted.append({"ply":len(board.move_stack),"uci":uci})
            if scenario=="R02" and is_bot and bot_index==1:
                event("connection_close_requested",path=qual.MOVE_PREFIX+uci,
                      request_id=rid)
                guarded.append({"event":"http_unknown_ack","owner_epoch":first,
                                "game_id":qual.GAME_ID,"endpoint":"move"})
            elif is_bot:
                event("response_write_completed",path=qual.MOVE_PREFIX+uci,
                      request_id=rid,status=200)
                guarded.append({"event":"http_move_ack_observed",
                                "owner_epoch":epoch,"game_id":qual.GAME_ID,
                                "move":uci})
    moves = [m.uci() for m in board.move_stack]
    complete = scenario!="R10"
    observations = (
        [{"phase":1,"epoch":first,"observation":"restart_after_lost_ack"},
         {"phase":2,"epoch":second,"observation":"complete_confirmed"}]
        if scenario=="R02" else
        [{"phase":1,"epoch":first,
          "observation":"rate_limited_suspension" if scenario=="R10"
                         else "complete_confirmed"}])
    result = {
        "schema_version":2,"run_id":key,"scenario":scenario,"color":color,
        "offline":True,"public_bot_release":False,"ready":True,
        "runner_errors":[],"http":requests,"transport_trace":rows,
        "game":{"game_id":qual.GAME_ID,"color":color,"moves":moves,
                "accepted":accepted,"complete":complete,
                "outcome":({"termination":"CHECKMATE"} if complete else None),
                "forbidden_attempts":[]},
        "commits":commits,"fault":{"activations":activations},
        "guard_events":guarded,"observations":observations,
        "journal":{"owner_epoch":observations[-1]["epoch"],
                   "intents":intents,
                   "games":[{"game_id":qual.GAME_ID,"history_json":json.dumps(moves),
                             "terminal":int(complete)}],
                   "cooldown":[{"singleton":1,"until_wall":1800000060.0,
                                "cause":"http429:move"}] if scenario=="R10" else [],
                   "events":[{
                       "event_id":1, "ts":1800000000.0, "epoch":first,
                       "event":"cooldown", "game_id":None,
                       "details":json.dumps({
                           "until_wall":1800000060.0,
                           "cause":"http429:move"},
                           sort_keys=True, separators=(",",":"))
                   }] if scenario=="R10" else []},
    }
    from deploy.lichess.reconcile import position_id
    for intent in result["journal"]["intents"]:
        intent["position_id"] = position_id(json.loads(intent["history_json"]))
    seal(result)
    return result


def seal(value):
    value["transport_trace_sha256"] = hashlib.sha256(
        qual.trace_bytes(value["transport_trace"])).hexdigest()


class PackagedMoveEvidenceTests(unittest.TestCase):
    def test_all_four_realistic_cases_have_valid_independent_witnesses(self):
        for key in qual.CASES:
            with self.subTest(key=key):
                result = qual.verify_smoke(fixture(key), key)
                self.assertTrue(result["independent_qualified"])
                self.assertFalse(result["public_bot_release"])

    def test_get_allowlist_matches_fake_api_without_wildcard(self):
        doc = fixture("r00-white")
        for endpoint in sorted(qual.http_contract.ALLOWED_GETS):
            doc["http"].append({"method":"GET", "path":endpoint,
                                "status":200, "monotonic_ms":30})
        self.assertTrue(qual.verify_smoke(doc, "r00-white")[
            "independent_qualified"])

    def test_forged_get_never_qualifies_producer_success(self):
        for path, status, rid in (
            ("/api/account/email",200,None),
            ("/api/account",401,None),
            ("/api/account",429,None),
            ("/api/stream/event",403,None),
            ("/api/stream/event",200,"request-1"),
            ("/api/account?private=true",200,None),
            ("/api/bot/game/stream/off3b001/../other",200,None),
        ):
            with self.subTest(path=path,status=status,rid=rid):
                doc=fixture("r00-white")
                doc["http"].append({"method":"GET","path":path,
                                    "status":status,"request_id":rid,
                                    "monotonic_ms":30})
                with self.assertRaisesRegex(ValueError,
                        "unapproved GET or POST|unexpected or malformed HTTP"):
                    qual.verify_smoke(doc,"r00-white")

    def test_position_illegal_uci_fails_even_when_the_producer_says_ready(self):
        doc=fixture("r10-white")
        uci="e7e5"  # syntactically valid, illegal on White's first turn
        doc["transport_trace"][0]["uci"]=uci
        doc["transport_trace"][0]["path"]=qual.MOVE_PREFIX+uci
        doc["http"][-1]["path"]=qual.MOVE_PREFIX+uci
        doc["journal"]["intents"][0]["move"]=uci
        doc["guard_events"][0]["uci"]=uci
        seal(doc)
        with self.assertRaisesRegex(ValueError,"position-illegal"):
            qual.verify_smoke(doc,"r10-white")

    def test_duplicate_submission_fails_independently_of_journal_status(self):
        doc=fixture("r10-white")
        final=doc["transport_trace"][-1]
        doc["transport_trace"].append({
            "seq":final["seq"]+1,"monotonic_ns":final["monotonic_ns"]+1_000_000,
            "event":"move_attempt","game_id":qual.GAME_ID,
            "ply_at_receipt":0,"uci":"e2e4","path":qual.MOVE_PREFIX+"e2e4"})
        seal(doc)
        with self.assertRaisesRegex(ValueError,"repeated intent"):
            qual.verify_smoke(doc,"r10-white")

    def test_unattributed_http_move_and_illegal_rejection_fail(self):
        for mutation in ("unknown-request","unexpected-status"):
            doc=fixture("r00-white")
            if mutation=="unknown-request":
                doc["http"].append({"method":"POST",
                    "path":qual.MOVE_PREFIX+"e7e5",
                    "status":400,"monotonic_ms":6000,
                    "request_id":"request-999"})
            else:
                doc["http"][2]["status"]=400
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                qual.verify_smoke(doc,"r00-white")

    def test_forged_trace_digest_or_commit_result_fails(self):
        doc=fixture("r00-black")
        doc["transport_trace"][0]["uci"]="a7a5"
        with self.assertRaisesRegex(ValueError,"trace digest"):
            qual.verify_smoke(doc,"r00-black")
        doc=fixture("r00-black")
        doc["commits"].pop()
        with self.assertRaisesRegex(ValueError,"commit extract"):
            qual.verify_smoke(doc,"r00-black")

    def test_r02_recovery_requires_a_distinct_confirmed_epoch(self):
        doc=fixture("r02-white")
        doc["observations"][1]["epoch"]=doc["observations"][0]["epoch"]
        doc["journal"]["owner_epoch"]=doc["observations"][0]["epoch"]
        with self.assertRaisesRegex(ValueError,"new-epoch"):
            qual.verify_smoke(doc,"r02-white")

    def test_r10_requires_correlated_completed_server_429(self):
        for mode in ("missing", "wrong-request", "duplicate", "wrong-status",
                     "before-fault"):
            doc = fixture("r10-white")
            rows = doc["transport_trace"]
            response = next(r for r in rows if
                            r["event"] == "response_write_completed")
            if mode == "missing":
                rows.remove(response)
            elif mode == "wrong-request":
                response["request_id"] = "request-999"
            elif mode == "duplicate":
                hold_index = next(i for i, row in enumerate(rows)
                                  if row["event"] == "rate_limit_hold_completed")
                rows.insert(hold_index, dict(response))
            elif mode == "wrong-status":
                response["status"] = 200
            else:
                response = rows.pop(rows.index(response))
                rows.insert(1, response)  # before the fault activation
            if mode in ("missing", "duplicate", "before-fault"):
                for i, row in enumerate(rows, start=1):
                    row["seq"] = i
                    if row["event"] != "rate_limit_hold_completed":
                        row["monotonic_ns"] = i * 1_000_000
            seal(doc)
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ValueError, "429|request correlation|status"):
                qual.verify_smoke(doc,"r10-white")

    def test_r10_requires_client_observed_rate_limit(self):
        for mode in ("missing", "bad-endpoint", "wrong-status",
                     "wrong-epoch", "duplicate"):
            doc = fixture("r10-white")
            evidence = doc["guard_events"]
            block = next(g for g in evidence
                         if g["event"] == "global_rate_limit_block")
            if mode == "missing":
                evidence.remove(block)
            elif mode == "bad-endpoint":
                block["endpoint"] = "challenge"
            elif mode == "wrong-status":
                block["status"] = 200
            elif mode == "wrong-epoch":
                block["owner_epoch"] = "z"*32
            else:
                evidence.append(dict(block))
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ValueError, "client guard"):
                qual.verify_smoke(doc,"r10-white")

    def test_r10_malformed_cooldown_row_fails_as_a_qualification_error(self):
        doc = fixture("r10-white")
        doc["journal"]["cooldown"] = [None]
        with self.assertRaisesRegex(ValueError, "durable 429"):
            qual.verify_smoke(doc, "r10-white")

    def test_r10_cooldown_event_and_deadline_falsifiers(self):
        for mode in ("text", "boolean", "infinite", "negative",
                     "short", "long", "missing-event", "wrong-epoch",
                     "wrong-cause", "wrong-event-deadline",
                     "wrong-guard-time"):
            doc = fixture("r10-white")
            cooldown = doc["journal"]["cooldown"][0]
            event = doc["journal"]["events"][0]
            if mode == "text":
                cooldown["until_wall"] = "garbage"
            elif mode == "boolean":
                cooldown["until_wall"] = True
            elif mode == "infinite":
                cooldown["until_wall"] = float("inf")
            elif mode == "negative":
                cooldown["until_wall"] = -2.0
            elif mode == "short":
                cooldown["until_wall"] = event["ts"] + 30
            elif mode == "long":
                cooldown["until_wall"] = event["ts"] + 86401
            elif mode == "missing-event":
                doc["journal"]["events"] = []
            elif mode == "wrong-epoch":
                event["epoch"] = "f"*32
            elif mode == "wrong-cause":
                event["details"] = json.dumps({
                    "cause":"http429:challenge",
                    "until_wall":cooldown["until_wall"]})
            elif mode == "wrong-event-deadline":
                event["details"] = json.dumps({
                    "cause":"http429:move","until_wall":1234.0})
            else:
                block = next(g for g in doc["guard_events"]
                             if g["event"] == "global_rate_limit_block")
                block["timestamp"] = 1700000000.0
            if mode in ("short", "long"):
                # Keep the JSON and persisted event in agreement, so the
                # duration-bound check itself must reject this mutation.
                event["details"] = json.dumps({
                    "until_wall":cooldown["until_wall"],
                    "cause":"http429:move"})
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ValueError, "cooldown|guard observation"):
                qual.verify_smoke(doc,"r10-white")

    def test_r10_rejects_durable_intent_without_unknown_ack(self):
        doc = fixture("r10-white")
        doc["journal"]["intents"][0]["status"] = "ACK_OBSERVED"
        with self.assertRaisesRegex(ValueError, "durable 429"):
            qual.verify_smoke(doc,"r10-white")

    def test_r10_window_cannot_be_counted_before_completed_response(self):
        doc = fixture("r10-white")
        response = next(r for r in doc["transport_trace"]
                        if r["event"] == "response_write_completed")
        hold = doc["transport_trace"][-1]
        response["monotonic_ns"] = hold["monotonic_ns"] - 500_000_000
        seal(doc)
        with self.assertRaisesRegex(ValueError, "post-429 observation"):
            qual.verify_smoke(doc, "r10-white")

    def test_r10_hold_event_must_bind_targeted_request(self):
        doc = fixture("r10-white")
        doc["transport_trace"][-1]["request_id"] = "request-unrelated"
        seal(doc)
        with self.assertRaisesRegex(ValueError, "post-429 observation"):
            qual.verify_smoke(doc, "r10-white")

    def test_r10_requires_complete_monotonic_observation(self):
        doc=fixture("r10-white")
        final=doc["transport_trace"][-1]
        fault=doc["transport_trace"][1]
        final["monotonic_ns"]=fault["monotonic_ns"]+59_000_000_000
        seal(doc)
        with self.assertRaisesRegex(ValueError,"61 seconds"):
            qual.verify_smoke(doc,"r10-white")

    def test_r10_rejects_all_api_requests_during_cooldown(self):
        doc=fixture("r10-white")
        doc["http"].append({"method":"GET","path":"/api/account/playing",
                            "status":200,"monotonic_ms":1200})
        with self.assertRaisesRegex(ValueError,"cooldown"):
            qual.verify_smoke(doc,"r10-white")

    def test_r02_completed_success_response_fails(self):
        doc = fixture("r02-white")
        close = next(x for x in doc["transport_trace"]
                     if x["event"] == "connection_close_requested")
        rid = close["request_id"]
        close["event"] = "response_write_completed"
        close["status"] = 200
        final = doc["transport_trace"][-1]
        doc["transport_trace"].append({
            "seq":final["seq"]+1,
            "monotonic_ns":final["monotonic_ns"]+1_000_000,
            "event":"connection_close_requested","path":close["path"],
            "request_id":rid})
        seal(doc)
        with self.assertRaisesRegex(ValueError,"lost-ack"):
            qual.verify_smoke(doc,"r02-white")

    def test_r02_unknown_ack_must_identify_move_endpoint(self):
        for mode in ("missing", "challenge", "token", "duplicate"):
            doc = fixture("r02-white")
            event = next(g for g in doc["guard_events"]
                         if g["event"] == "http_unknown_ack")
            if mode == "missing":
                del event["endpoint"]
            elif mode in ("challenge", "token"):
                event["endpoint"] = mode
            else:
                doc["guard_events"].append({
                    **event, "endpoint": "challenge"})
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    ValueError, "lost-ack"):
                qual.verify_smoke(doc, "r02-white")

    def test_r02_unrelated_completed_responses_are_allowed(self):
        doc = fixture("r02-white")
        target = next(g["request_id"] for g in doc["transport_trace"]
                      if g["event"] == "fault_activated")
        completed = [r for r in doc["transport_trace"]
                     if r["event"] == "response_write_completed"
                     and r.get("request_id") != target]
        self.assertTrue(completed)
        self.assertTrue(qual.verify_smoke(doc,"r02-white")["independent_qualified"])

    def test_r02_rejects_missing_unknown_ack_or_successful_ack(self):
        for mode in ("missing-unknown", "acknowledged"):
            doc = fixture("r02-white")
            if mode == "missing-unknown":
                doc["guard_events"] = [g for g in doc["guard_events"]
                                       if g["event"] != "http_unknown_ack"]
            else:
                doc["guard_events"].append({
                    "event":"http_move_ack_observed","game_id":qual.GAME_ID,
                    "owner_epoch":doc["observations"][0]["epoch"],"move":"f2f3"})
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,"lost-ack"):
                qual.verify_smoke(doc,"r02-white")


    def test_r02_rejects_a_successful_response_without_request_identity(self):
        doc = fixture("r02-white")
        close = next(t for t in doc["transport_trace"]
                     if t["event"] == "connection_close_requested")
        final = doc["transport_trace"][-1]
        doc["transport_trace"].append({
            "seq":final["seq"]+1,
            "monotonic_ns":final["monotonic_ns"]+1_000_000,
            "event":"response_write_completed",
            "path":close["path"],"status":200})
        seal(doc)
        with self.assertRaisesRegex(ValueError, "request correlation"):
            qual.verify_smoke(doc,"r02-white")



if __name__=="__main__":
    unittest.main()
