#!/usr/bin/env python3
"""Independent read-only ONLINE-PLAY-1 fake API verdict.

Never imports producer test-runner code or accepts self-reported PASS strings.
Requires both colors, exact-head source, bound gateway/HTTP/move/SQLite/PGN,
real two-container isolation receipts and terminal cleanup. Not live approval.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
import chess
import chess.pgn

ROOT=Path(__file__).resolve().parents[1]
class EvidenceFailure(ValueError):pass
def require(cond,why):
    if not cond:raise EvidenceFailure(why)
def read(path):
    path=Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<12*1024*1024,
            "evidence file missing, linked or oversized: "+str(path))
    return json.loads(path.read_text(encoding="utf-8"))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _history(tokens):
    require(isinstance(tokens,list),"UCI history must be full list")
    board=chess.Board()
    moves=[]
    for uci in tokens:
        require(isinstance(uci,str),"invalid UCI move type")
        try: move=chess.Move.from_uci(uci)
        except ValueError as exc:raise EvidenceFailure("malformed game UCI") from exc
        require(move!=chess.Move.null() and move in board.legal_moves,
                "independent legal move check failed")
        board.push(move);moves.append(move.uci())
    return board,moves
def _pgn(text,history):
    require(isinstance(text,str) and len(text)<1000000,"PGN missing/oversized")
    stream=io.StringIO(text)
    game=chess.pgn.read_game(stream)
    require(game is not None and not game.errors,"PGN parsing error")
    require(chess.pgn.read_game(stream) is None,"multiple or malformed PGN games")
    moves=[m.uci() for m in game.mainline_moves()]
    require(moves==history,"PGN move history differs from authoritative UCI")
    return game

def verify_one(path:Path,expected_color:str,sha_source:str):
    result=read(path/"integration.json")
    server=read(path/"gateway/server.json")
    gateway=read(path/"gateway/gateway-state.json")
    bridge=read(path/"bridge/report.json")
    require(result.get("mode")=="offline-fake-only" and
            result.get("compose_cleanup_ok") is True and
            not (path/"cleanup-incomplete.json").exists() and
            result.get("live_game_authorized") is False and
            result.get("public_bot_release") is False,
            "fake run acquired real/public authority")
    require(result.get("color")==expected_color and result.get("source_sha")==sha_source,
            "source/color mismatch")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}",result.get("image_digest","")) is not None,
            "private test image digest missing")
    files={
        "gateway/server.json":server,
        "gateway/gateway-state.json":gateway,
        "bridge/report.json":bridge,
    }
    for name in files:
        require(sha(path/name)==result.get("evidence",{}).get(name),
                "artifact digest mismatch: "+name)
    host=result.get("host_observation",{})
    net=host.get("network_tests",{})
    require(host.get("scope")=="synthetic-ci-fixture-only" and
            host.get("image_identity_verified") is True and
            host.get("source_mounts_read_only") is True and
            host.get("operator_envelope_met") is True and
            host.get("durable_volume_separate") is True and
            host.get("destination_host_qualified") is False and
            host.get("live_game_authorized") is False,
            "host test result improperly promoted")
    for key in ("bridge_egress_test_pass","gateway_private_port_test_pass",
                "separate_network_namespaces","bridge_only_internal",
                "gateway_dual_homed","no_host_ports","gateway_only_secret_mount",
                "bridge_external_tcp_denied"):
        require(net.get(key) is True,"missing independent network negative: "+key)
    storage=host.get("storage_tests",{})
    require(isinstance(storage,dict) and
            all(storage.get(k) is True for k in (
                "persistent_separate","scratch_separate","exec_stage_mount_verified")),
            "separately mounted executable staging was not verified")
    cp=host.get("capacity",{})
    require(cp.get("operator_minimums_met") is True and cp.get("capacity_complete") is True,
            "measured cgroups/CPU/memory not sufficient for simulator")
    require(server.get("complete") is True and server.get("challenge_accepted") is True,
            "fake server did not finish accepted game")
    require(server.get("color")==expected_color,"server color drift")
    gid=server.get("game_id")
    require(isinstance(gid,str) and re.fullmatch(r"[A-Za-z0-9]{8}",gid) is not None,
            "fake game ID invalid")
    # Source-independent inspection of actual fake API challenge + first
    # gameFull. A PGN alone cannot prove the approved BOT/clock admission.
    incoming=server.get("challenge")
    full=server.get("game_full")
    require(isinstance(incoming,dict) and incoming.get("type")=="challenge"
            and isinstance(incoming.get("challenge"),dict),
            "original challenge event not retained")
    ch=incoming["challenge"]
    require(ch.get("id")==gid and ch.get("rated") is False
            and ch.get("speed")=="rapid" and ch.get("initialFen")=="startpos"
            and isinstance(ch.get("variant"),dict)
            and ch["variant"].get("key")=="standard",
            "challenge rated/variant/startpos drift")
    tc=ch.get("timeControl",{})
    require(isinstance(tc,dict) and tc.get("type")=="clock"
            and type(tc.get("limit")) is int and tc["limit"]==600
            and type(tc.get("increment")) is int and tc["increment"]==5,
            "challenge must be exactly casual rapid 600+5")
    for field,expected in (("challenger","approved_test_bot"),("destUser","allfatherfixture")):
        part=ch.get(field,{})
        require(isinstance(part,dict) and
                str(part.get("name","")).casefold()==expected and
                str(part.get("title","")).upper()=="BOT",
                "incoming challenge participant not approved BOT")
    require(isinstance(full,dict) and full.get("type")=="gameFull"
            and full.get("id")==gid and full.get("rated") is False
            and full.get("speed")=="rapid" and full.get("initialFen")=="startpos"
            and isinstance(full.get("variant"),dict)
            and full["variant"].get("key")=="standard",
            "first authoritative gameFull missing or altered")
    start_clock=full.get("clock",{})
    require(isinstance(start_clock,dict) and type(start_clock.get("initial")) is int
            and start_clock["initial"]==600000
            and type(start_clock.get("increment")) is int
            and start_clock["increment"]==5000,
            "initial gameFull clock mismatch")
    expected_owner_color=expected_color
    for side in ("white","black"):
        expected_name="allfatherfixture" if side==expected_owner_color else "approved_test_bot"
        player=full.get(side,{})
        require(isinstance(player,dict) and
                str(player.get("name","")).casefold()==expected_name and
                str(player.get("title","")).upper()=="BOT",
                "gameFull player BOT identity/color mismatch")
    initial=full.get("state",{})
    require(isinstance(initial,dict) and initial.get("type")=="gameState"
            and initial.get("moves")=="",
            "first gameFull was not bound to start position")
    board,moves=_history(server.get("moves"))
    require(board.is_game_over(claim_draw=False),"game did not terminate by rules")
    outcome=board.outcome(claim_draw=False)
    require(outcome is not None,"no natural rules outcome")
    expected_result=outcome.result()
    recorded=server.get("outcome",{})
    require(isinstance(recorded,dict) and
            recorded.get("status") in ("mate","draw") and
            recorded.get("winner")==("white" if outcome.winner else "black" if outcome.winner is False else None),
            "server terminal result mismatch")
    pgn=_pgn(server.get("pgn"),moves)
    require(pgn.headers.get("Result") in (expected_result,"*"),
            "PGN result header contradicted natural result")
    accepted=server.get("accepted")
    require(isinstance(accepted,list) and len(accepted)>0,
            "sealed engine never emitted an accepted move")
    indices=set()
    for item in accepted:
        require(isinstance(item,dict) and type(item.get("ply")) is int and
                1<=item["ply"]<=len(moves) and item.get("uci")==moves[item["ply"]-1],
                "server engine move evidence is not a legal occurrence")
        require(item["ply"] not in indices,"duplicate accepted move")
        indices.add(item["ply"])
        require((item["ply"]%2==1)==(expected_color=="white"),
                "engine moved out of its color")
    req=server.get("requests",[])
    require(isinstance(req,list) and req,"no actual fake HTTP server activity")
    allowed_get={"/api/account","/api/account/playing","/api/stream/event",
                 f"/api/bot/game/stream/{gid}"}
    allowed_post={"/api/token/test",f"/api/challenge/{gid}/accept"}
    for row in req:
        require(isinstance(row,dict),"malformed HTTP request evidence")
        method,path=row.get("method"),row.get("path")
        if method=="GET":require(path in allowed_get,"unauthorized GET reached fake API")
        elif method=="POST":
            if path not in allowed_post:
                require(re.fullmatch(r"/api/bot/game/"+gid+r"/move/[a-h][1-8][a-h][1-8][qrbn]?",str(path)) is not None,
                        "unexpected POST reached fake API")
        else:raise EvidenceFailure("unsupported HTTP method reached fake API")
        require(row.get("status")==200,"unexpected HTTP response in successful game")
    def count(method,path):
        return sum(1 for r in req if r.get("method")==method and r.get("path")==path)
    require(count("POST","/api/token/test")==1,"real-token test called multiple times")
    require(count("POST",f"/api/challenge/{gid}/accept")==1,"challenge accept not one-shot")
    require(count("GET","/api/stream/event")==1,"duplicate global token stream")
    require(count("GET",f"/api/bot/game/stream/{gid}")>=1,"game stream missing")
    move_posts=[r for r in req if r.get("method")=="POST" and
                str(r.get("path","")).startswith(f"/api/bot/game/{gid}/move/")]
    require(len(move_posts)==len(accepted),"accepted move attempts not one-to-one")
    for post,source_move in zip(move_posts,accepted):
        require(post.get("path")==f'/api/bot/game/{gid}/move/{source_move["uci"]}',
                "chronological HTTP move occurrence differs from accepted server move")
    require(not server.get("forbidden_attempts"),"actual forbidden requests reached server")
    require(bridge.get("source_sha")==sha_source and bridge.get("game_id")==gid and
            bridge.get("status")=="TERMINAL_DURABLE" and bridge.get("terminal") is True,
            "real pinned bridge/supervisor not independently terminal")
    manifest=bridge.get("executed_source",{})
    upstream=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
    expected_upstream={
        "lib/lichess.py":"891a7715271a1f86a6f032753ca9f6f6d76c421e",
        "lib/engine_wrapper.py":"38c13f993bb4e4bf9d052f2f58e019f97030b2b0",
        "lib/lichess_bot.py":"63ab211eb433db73d1fecc47074232699dba0e94",
    }
    require(manifest.get("sealed_release_source_commit")==sha_source
            and manifest.get("upstream_commit")==upstream["commit"],
            "executed UCI/lichess-bot source identity differs from exact HEAD")
    for key in ("sealed_release_manifest_sha256","sealed_engine_launcher_sha256",
                "live_effective_config_sha256","live_handler_sha256"):
        require(isinstance(manifest.get(key),str) and re.fullmatch(
            r"[0-9a-f]{64}",manifest[key]) is not None,
            "executed file-byte identity missing: "+key)
    require(manifest.get("live_handler_sha256")==sha(ROOT/"deploy/lichess/live_extra_game_handlers.py")
            and manifest.get("live_policy_sha256")==sha(ROOT/"qualification/online-live-canary-v1.json"),
            "effective live challenge/policy bytes drift")
    patches=manifest.get("patched_upstream",{})
    require(isinstance(patches,dict) and set(patches)==set(expected_upstream),
            "pinned upstream patched-source map not closed")
    for upstream_path,locked in expected_upstream.items():
        entry=patches[upstream_path]
        require(isinstance(entry,dict) and entry.get("git_blob_sha1")==locked and
                re.fullmatch(r"[0-9a-f]{64}",entry.get("patched_sha256","")) is not None,
                "pinned upstream Git blob/overlay drift")
    require(manifest.get("live_profile")=="source-locked-live-canary-v1",
            "runtime did not stage approved live transport profile")
    live_modules=manifest.get("live_modules",{})
    require(live_modules=={
        "live_gateway_policy.py":sha(ROOT/"deploy/lichess/live_gateway_policy.py")},
        "executed live gateway policy not source-sealed")
    guard=manifest.get("guard_modules",{})
    expected_guard=("move_journal.py","reconcile.py","session_owner.py","guarded_transport.py")
    require(isinstance(guard,dict) and set(guard)==set(expected_guard),
            "guard overlay source closure incomplete")
    for name in expected_guard:
        require(guard[name]==sha(ROOT/"deploy/lichess"/name),
                "executed guarded bridge source differs from exact HEAD: "+name)
    processes=bridge.get("processes",{})
    require(processes.get("pid_namespace_isolated") is True and
            processes.get("live_remaining")==0 and
            processes.get("kill_switch_activated") is True,
            "bridge process cleanup missing")
    game=_pgn(bridge.get("game_pgn"),moves)
    require(game.headers.get("Result")==expected_result,
            "bridge PGN result not synchronized with server")
    require(game.headers.get("TimeControl")=="600+5",
            "canonical 10+5 clock not retained")
    journal=bridge.get("journal",{})
    require(journal.get("owner_epoch") and isinstance(journal.get("events"),list),
            "durable owner/SQLite audit absent")
    games=journal.get("games",[])
    require(len(games)==1 and games[0].get("game_id")==gid and
            games[0].get("terminal")==1 and json.loads(games[0]["history_json"])==moves,
            "journal did not reconcile terminal server game")
    intents=journal.get("intents",[])
    require(len(intents)==len(accepted),"engine accepted moves lack one intent each")
    for item in accepted:
        matches=[j for j in intents if j.get("game_id")==gid and
                 j.get("ply_before")==item["ply"]-1 and
                 j.get("move")==item["uci"] and
                 j.get("status")=="CONFIRMED"]
        require(len(matches)==1,"server-accepted move not durably confirmed once")
    # Recompute real per-move engine replay hashes from retained bounded
    # manifests, rather than accepting a producer's claim that UCI work ran.
    index=bridge.get("replay_index")
    require(isinstance(index,list) and len(index)>=len(accepted) and len(index)<=128,
            "one or more sealed UCI moves lacks a retained replay manifest")
    seen=set()
    for row in index:
        require(isinstance(row,dict),"malformed replay evidence row")
        rid=row.get("run_id")
        require(isinstance(rid,str) and re.fullmatch(r"[A-Za-z0-9._-]{1,120}",rid)
                and rid not in (".","..") and rid not in seen,
                "duplicate or unsafe replay run identity")
        seen.add(rid)
        expected=f"replays/{rid}/manifest.json"
        require(row.get("manifest")==expected,
                "replay evidence path not occurrence-scoped")
        replay_manifest=path/"bridge"/expected
        require(replay_manifest.is_file() and not replay_manifest.is_symlink()
                and replay_manifest.stat().st_size<=2*1024*1024 and
                sha(replay_manifest)==row.get("sha256"),
                "replay manifest byte identity not retained")
        record=read(replay_manifest)
        require(record.get("generation")==row.get("generation")
                and (record.get("position") or {}).get("position_id")==row.get("position_id")
                and record.get("outward_decision")==row.get("outward_decision"),
                "replay projection does not match raw sealed engine manifest")
        if "route_sha256" in row:
            route=path/"bridge"/"replays"/rid/"route.json"
            require(route.is_file() and not route.is_symlink() and
                    route.stat().st_size<=2*1024*1024 and
                    sha(route)==row["route_sha256"],
                    "sealed engine routing evidence hash mismatch")
    lease=bridge.get("one_game_lease",{})
    require(lease.get("game")==gid and lease.get("terminal")==1,
            "second-game lockout not persisted")
    require(gateway.get("game")==gid and gateway.get("terminal")==1 and
            gateway.get("accepted")==1 and gateway.get("game_full")==1,
            "gateway did not bind actual gameFull and terminal state")
    posts=gateway.get("posts")
    require(isinstance(posts,list) and len(posts)==len(accepted),
            "durable gateway one-shot move record missing")
    require(len({(p["game"],p["ply"]) for p in posts})==len(posts),
            "gateway duplicate move POST attempt")
    # The gateway may be deliberately suspended AFTER a verified terminal
    # by the supervisor kill switch. Terminal and move/journal evidence above
    # must still match; an early suspended game never reaches these gates.
    if gateway.get("suspended"):
        require(gateway.get("terminal")==1,
                "gateway suspended without authoritative terminal")
    return {"color":expected_color,"game_id":gid,"total_plies":len(moves),
            "sealed_engine_moves":len(accepted),
            "terminal_result":expected_result,
            "http_requests":len(req),"move_posts":len(move_posts),
            "journal_intents":len(intents),"network_isolated":True,
            "owner_epoch_verified":True,
            "retained_engine_replays":len(index),
            "executed_source_identity":manifest["sealed_release_manifest_sha256"]}

def verify(root:Path,source_sha:str):
    require(re.fullmatch(r"[0-9a-f]{40}",source_sha or "") is not None,
            "exact-head source SHA required")
    current=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],
                                    text=True,timeout=12).strip()
    require(current==source_sha,"independent verifier checkout not exact HEAD")
    policy=read(ROOT/"qualification/online-live-canary-v1.json")
    require(policy.get("live_game_authorized") is False and
            policy.get("public_bot_release") is False and
            policy.get("operator_armed") is False,
            "policy attempts promotion")
    results=[]
    for color in ("white","black"):
        results.append(verify_one(root/color,color,source_sha))
    require(results[0].get("executed_source_identity")==results[1].get("executed_source_identity"),
            "real sealed engine source changed across color games")
    return {"schema_version":1,"verdict":"DRY_RUN_PASS",
            "scope":"fake-lichess-api-only","source_sha":source_sha,
            "games":results,"live_game_authorized":False,
            "public_bot_release":False,
            "license_review_completed":False}

def _failure_diagnostic(exc: BaseException) -> dict[str, object]:
    """Return ONLY source-controlled code identity/line, never exception text.

    The independent verifier may read hostile evidence paths, payloads and
    JSON. Formatting arbitrary exception strings into uploaded artifacts is
    prohibited. Traceback frame identity is matched to the exact functions
    in this source file, so the bounded gate is independently inspectable.
    """
    trusted={
        fn.__code__:name for name,fn in (
            ("verify_one",verify_one),("verify",verify),
            ("_history",_history),("_pgn",_pgn),("read",read),
        )
    }
    selected={"function":"UNCLASSIFIED","line":0}
    tb=exc.__traceback__
    while tb is not None:
        name=trusted.get(tb.tb_frame.f_code)
        if name is not None and type(tb.tb_lineno) is int and 1<=tb.tb_lineno<=1000:
            selected={"function":name,"line":tb.tb_lineno}
        tb=tb.tb_next
    allowed={"EvidenceFailure","OSError","ValueError","KeyError","TypeError",
             "JSONDecodeError","CalledProcessError","TimeoutExpired",
             "FileNotFoundError","PermissionError"}
    label=type(exc).__name__
    return {"error_type":label if label in allowed else "UNCLASSIFIED",
            "source_gate":selected}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--policy",type=Path,default=ROOT/"qualification/online-live-canary-v1.json")
    p.add_argument("--evidence",type=Path,required=True)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--mode",choices=("offline-dry-run",),required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    outcome={"schema_version":1,"verdict":"INCONCLUSIVE","scope":"fake-lichess-api-only",
             "source_sha":a.source_sha,"live_game_authorized":False,
             "public_bot_release":False,"failures":[]}
    try:
        if a.policy.resolve()!=(ROOT/"qualification/online-live-canary-v1.json").resolve():
            raise EvidenceFailure("arbitrary policy override not allowed")
        outcome=verify(a.evidence,a.source_sha)
        status=0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        diagnostic=_failure_diagnostic(exc)
        outcome["failures"]=[diagnostic["error_type"]]
        outcome["failure_gate"]=diagnostic["source_gate"]
        status=2
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(outcome,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"verdict":outcome["verdict"],
                      "failures":outcome.get("failures",[]),
                      "live_game_authorized":False}))
    return status

if __name__=="__main__":raise SystemExit(main())
