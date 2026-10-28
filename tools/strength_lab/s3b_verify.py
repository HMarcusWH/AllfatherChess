"""Independent read-only S3-B raw engine/move/evidence verification.

Producer-provided PASS has zero authority. Reconstruct each actual proposer
and comparator frame, verify S2 single-writer flight and resource disclosures.
A complete research replay still reports NO move/strength/resource authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from controller.strength.contracts import (TurnKey, SearchIdentity,
                                            IncumbentCheckpoint,
                                            StrengthContractError, digest,
                                            require, BESTMOVE)
from controller.strength.candidates import reconstruct_proposal,make_pair
from controller.strength.proposer import ProposerRequestV1,reconstruct_proposer
from controller.strength.comparison import (ComparisonRequestV1,
                                             reconstruct_comparison)
from controller.strength.integrity import strict_load, verify_flight
from tools.strength_lab.e00_runner import sha
from tools.strength_lab.s3a_rules import legal_roots,verify_proposal

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE_ROOT=ROOT/"build/test-results/strength-s3b"
POLICY=ROOT/"qualification/strength/s3b-real-probe-v1.json"


def _typed_turn(raw:object)->TurnKey:
    require(type(raw) is dict and set(raw)==set(TurnKey.__dataclass_fields__) |
            {"kind","schema_version"},"bad S3B turn serialization")
    obj=TurnKey(**{x:raw[x] for x in TurnKey.__dataclass_fields__})
    require(obj.as_dict()==raw,"S3B turn noncanonical")
    return obj


def _search(raw:object)->SearchIdentity:
    require(type(raw) is dict and
            set(raw)==set(SearchIdentity.__dataclass_fields__)|{"kind","schema_version"},
            "bad S3B search identity")
    obj=SearchIdentity(_typed_turn(raw["turn"]),raw["source_commit"],
                       raw["profile_sha256"],raw["binary_sha256"],
                       raw["options_sha256"])
    require(obj.as_dict()==raw,"S3B search not canonical")
    return obj


def _checkpoint(raw:object)->IncumbentCheckpoint:
    require(type(raw) is dict and
            set(raw)==set(IncumbentCheckpoint.__dataclass_fields__) |
            {"kind","schema_version"},"bad S3B checkpoint")
    obj=IncumbentCheckpoint(**{
        k:raw[k] for k in IncumbentCheckpoint.__dataclass_fields__})
    require(obj.as_dict()==raw,"S3B checkpoint is not source-canonical")
    return obj


def _proposer_request(raw:dict)->ProposerRequestV1:
    req=ProposerRequestV1(
        anchor_search=_search(raw["anchor_search"]),
        proposer_search=_search(raw["proposer_search"]),
        incumbent=_checkpoint(raw["incumbent"]),
        position_command=raw["position_command"],
        legal_roots=tuple(raw["legal_roots"]),
        external_roots=tuple(raw["external_roots"]),
        family=raw["family"],engine_sha256=raw["engine_sha256"],
        network_sha256=raw["network_sha256"],
        native_limit_nodes=raw["native_limit_nodes"],
        proposal_nonce=raw["proposal_nonce"])
    require(req.as_dict()==raw,"S3B proposer request noncanonical/forged")
    return req


def _comparator_request(raw:dict,proposal,source_sha:str)->ComparisonRequestV1:
    pair=make_pair(proposal,proposal.incumbent)
    req=ComparisonRequestV1(
        pair=pair,comparator_search=_search(raw["comparator_search"]),
        comparator_engine_sha256=raw["comparator_engine_sha256"],
        comparator_nnue_sha256=raw["comparator_nnue_sha256"],
        source_bundle_manifest_sha256=source_sha,
        round_number=raw["round_number"],
        native_limit_nodes=raw["native_limit_nodes"],
        round_nonce=raw["round_nonce"])
    require(req.as_dict()==raw,"S3B comparator source/roots/nonce changed")
    return req


def _flight(row:dict)->None:
    item=row["flight"]
    require(type(item) is dict and set(item)=={"flight","flight_sha256"},
            "missing S3B source-flight bytes")
    f=item["flight"]
    v=verify_flight(f)
    require(v["status"]=="PUBLISHED_ANCHOR" and v["replacement_count"]==0
            and v["qualified"] is False, "source S3 flight does not authorize real anchor")
    starts=[x["data"] for x in f["events"] if x["event"]=="begin"]
    outs=[x["data"] for x in f["events"] if x["event"]=="published"]
    require(len(starts)==len(outs)==1 and
            starts[0]["turn"]["history_sha256"]==row["turn"]["history_sha256"] and
            starts[0]["search"]["source_commit"]==row["source_commit"] and
            outs[0]["chosen_line"]==row["anchor_terminal"],
            "source flight does not match emitted native anchor bestmove")
    require(f["kind"]=="strength_s2_flight_v1",
            "S3B flight accidentally impersonates legacy G3 evidence")
    frozen=(json.dumps(f,sort_keys=True,indent=2,
                       ensure_ascii=True,allow_nan=False)+"\n").encode("ascii")
    require(hashlib.sha256(frozen).hexdigest()==item["flight_sha256"],
            "S3B retained S2 flight file bytes differ from signed origin hash")


def _checked(path:Path,root:Path)->Path:
    require(path.is_file() and not path.is_symlink() and
            path.resolve().is_relative_to(root.resolve()) and
            path.stat().st_size<16*1024*1024,
            "missing/oversize/linked S3B artifact")
    return path


def verify(directory:Path)->dict[str,Any]:
    directory=directory.resolve(strict=True)
    require(directory.parent==EVIDENCE_ROOT.resolve() and
            directory.is_dir() and not directory.is_symlink(),
            "S3B verification source must be a direct owned run folder")
    # Strict set: an orphan or substituted report cannot be a successful run.
    files={p.name for p in directory.iterdir()}
    require(files=={"experiment.json","artifact-index.json"},
            "S3B missing/unexpected evidence artifact")
    record=strict_load(_checked(directory/"experiment.json",directory))
    index=strict_load(_checked(directory/"artifact-index.json",directory))
    require(type(record) is dict and
            record.get("schema_version")==1 and
            record.get("kind")=="strength_s3b_offline_diagnostic_v1",
            "S3B experiment schema changed")
    require(type(index) is dict and
            set(index)=={"schema_version","kind","experiment_sha256",
                         "source_commit","source_policy_sha256","authority"} and
            index["schema_version"]==1 and
            index["kind"]=="strength_s3b_artifact_index_v1" and
            index["authority"]=={"move":False,"public":False,"release":False},
            "S3B artifact index claims authority")
    require(sha(directory/"experiment.json")==index["experiment_sha256"] and
            sha(POLICY)==index["source_policy_sha256"] and
            record["policy_sha256"]==sha(POLICY),
            "S3B retained evidence/policy bytes drifted")
    source=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],
                                   text=True).strip()
    from tools.strength_lab.s3b_runner import preflight
    assets=preflight()
    require(record["bundle_manifest_sha256"]==assets["manifest_sha256"],
            "S3B comparator used a foreign or altered source bundle")
    require(record["family"] in ("lc0","reckless") and
            record["external_go_command"] in assets["policy"]["supported_native_go_commands"],
            "S3B off-policy native UCI clock")
    require(source==record["source_commit"]==index["source_commit"],
            "S3B source identity differs from exact Git commit")
    for claim in ("resource_qualified","strength_qualified","equal_compute",
                  "move_authorized","public_bot_release","live_game_authorized"):
        require(record[claim] is False,"S3B producer claims unearned authority: "+claim)
    turn=_typed_turn(record["turn"])
    anchor=_search(record["anchor_search"])
    anchor_options=assets["s3a"]["instances"]["stockfish-anchor"]["options"]
    require(anchor.binary_sha256==sha(assets["inputs"]["stockfish_engines"]) and
            anchor.options_sha256==digest({"options":anchor_options,"args":[]}),
            "S3B native Stockfish executable/options do not match source")
    require(anchor.turn==turn and
            anchor.source_commit==source and
            anchor.profile_sha256==record["source_profile_sha256"] and
            anchor.search_digest and
            record["source_profile_sha256"]==sha(ROOT/
                "config/allfather.strength-s3a-observe.experimental.json"),
            "S3B anchor/profile changed")
    require(turn.history_sha256==hashlib.sha256(
                record["position_command"].encode()).hexdigest(),
            "S3B raw full position history does not reconstruct")
    roots=legal_roots(record["position_command"])
    require(tuple(record["legal_roots"])==roots,
            "S3B independently reconstructed legal root universe disagrees")
    assert_terminal=record["anchor_terminal"]
    if assert_terminal is not None:
        matched=BESTMOVE.fullmatch(assert_terminal)
        require(matched is not None and matched.group(1) in roots,
                "S3B anchor output is not a legal source move")
        _flight(record)
    else:
        require(record["status"]=="INCOMPLETE" or bool(record["errors"]),
                "S3B producer suppressed an actual anchor output")
    streams=record.get("bounded_raw_uci")
    require(type(streams) is dict and set(streams)=={"anchor","helper","comparator"}
            and all(type(rows) is list and len(rows)<=500 and
                    all(type(line) is str and len(line)<=4096 for line in rows)
                    for rows in streams.values()),
            "S3B source UCI transcript absent/oversized")
    def commands(name):
        return [line[3:] for line in streams[name] if line.startswith(">> go ")]
    def terminals(name):
        return [line[3:] for line in streams[name] if line.startswith("<< bestmove ")]
    if record["anchor_terminal"] is not None:
        require(terminals("anchor")==[record["anchor_terminal"]] and
                (not commands("anchor") or
                 commands("anchor")==[record["external_go_command"]]),
                "S3B actual anchor UCI differs from published source")
    proposal_seen=False
    comparisons=0
    proposal=None
    if record["proposer"] is not None:
        item=record["proposer"]
        req=_proposer_request(item["request"])
        require(commands("helper")==[req.search_command] and
                terminals("helper")==[item["terminal"]],
                "S3B real proposer UCI go/terminal differs from record")
        legacy_spec=assets["legacy"]["instances"][req.family+"-shadow"]
        expected_opts=dict(legacy_spec["options"])
        if req.family=="lc0":
            require(assets["policy"]["proposer"]["lc0"]["multipv"]==2,
                    "S3B LC0 native variant not source-frozen")
            expected_opts["MultiPV"]=2
        require(req.proposer_search.binary_sha256==
                sha(assets["inputs"][req.family+"_engines"]) and
                req.proposer_search.options_sha256==digest({
                    "options":expected_opts,
                    "args":legacy_spec.get("args",[])}) and
                (req.family!="lc0" or req.network_sha256==
                 sha(assets["inputs"]["lc0_networks"])),
                "S3B source-locked proposer binary/options/network drift")
        require(req.anchor_search==anchor and req.position_command==
                record["position_command"] and req.family==record["family"],
                "S3B proposed under a foreign source/search")
        candidate=item["proposal"]
        fallback="0"*64
        grant=fallback if candidate is None else candidate["grant_digest"]
        settlement=fallback if candidate is None else candidate["settlement_sha256"]
        actual=reconstruct_proposer(req,item["observations"],item["terminal"],
                                    terminal_ns=item["terminal_ns"],
                                    grant_digest=grant,settlement_sha256=settlement)
        require(actual==item,"S3B proposer transcript/score/native work tampered")
        if candidate is not None:
            proposal=reconstruct_proposal(candidate)
            verify_proposal(candidate)
            proposal_seen=True
    for i,raw in enumerate(record["comparison_rounds"],1):
        require(proposal is not None,"S3B comparator without real legal challenger")
        req=_comparator_request(raw["request"],proposal,
                                record["bundle_manifest_sha256"])
        require(len(commands("comparator"))>=i and
                commands("comparator")[i-1]==req.go_command and
                len(terminals("comparator"))>=i and
                terminals("comparator")[i-1]==raw["terminal_line"],
                "S3B actual Stockfish UCI go/terminal disagrees with evidence")
        comparator_options=dict(anchor_options)
        comparator_options.update({"Threads":1,"Hash":128,"MultiPV":2})
        require(req.round_number==i and req.comparator_search.binary_sha256==
                anchor.binary_sha256 and
                req.comparator_engine_sha256==sha(assets["inputs"]["stockfish_engines"]) and
                req.comparator_nnue_sha256==sha(assets["inputs"]["stockfish_networks"]) and
                req.comparator_search.options_sha256==digest({
                    "options":comparator_options,"args":[]}),
                "S3B comparator binary/NNUE/options do not match exact anchor")
        actual=reconstruct_comparison(req,raw["observations"],
                                      raw["terminal_line"],
                                      completed_ns=raw["terminal_ns"])
        require(actual==raw,"S3B raw MultiPV/terminal transcript altered")
        comparisons+=1
    require(comparisons<=2,"S3B unapproved comparator budget/round inflation")
    if record["status"]=="COMPARISON_RECORDED_UNQUALIFIED":
        require(len(commands("helper"))==len(terminals("helper"))==1 and
                len(commands("comparator"))==len(terminals("comparator"))==comparisons,
                "S3B claimed full comparison without exact real UCI terminal counts")
    require(len(commands("anchor"))<=1,"S3B native anchor sent duplicate go")
    if record.get("optional_denial") is not None:
        require(record["optional_denial"]=="STALE_DISPATCH_REFUSED"
                and record["status"]=="OPTIONAL_ABSTAINED_STALE"
                and record["scheduler"] is not None
                and record["scheduler"]["state"]=="ABORTED"
                and not record["comparison_rounds"]
                and record["proposer"] is None,
                "S3B optional denial does not reconstruct to an aborted flight")
    if record["status"]=="COMPARISON_RECORDED_UNQUALIFIED":
        require(proposal_seen and comparisons==2 and
                all(x["disposition"]=="COMPLETE_CP"
                    for x in record["comparison_rounds"]) and
                record["anchor_terminal"] is not None and
                not record["errors"] and
                record["scheduler"]["state"]=="DONE",
                "S3B producer reported completed comparison without evidence")
    return {"schema_version":1,"kind":"strength_s3b_independent_verdict_v1",
            "execution_integrity":"INCOMPLETE" if record["errors"] else "RECORDED",
            "producer_status":record["status"],
            "failure_reasons":[str(x)[:220] for x in record["errors"][:10]],
            "source_commit":source,
            "real_proposer_observed":record["proposer"] is not None,
            "actual_proposal_observed":proposal_seen,
            "comparator_rounds_reconstructed":comparisons,
            "native_anchor_published":assert_terminal is not None,
            "resource_domain_qualified":False,
            "playing_strength_qualified":False,
            "quality_witness_qualified":False,
            "equal_compute":False,
            "move_authorized":False,
            "release_authorized":False,
            "public_bot_release":False}
