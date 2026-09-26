"""Shared parsing and audit helpers for LOCAL-1 full-game qualification."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from common.search_request import STARTPOS_FEN, parse_position_command

MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
HEADER_RE = re.compile(r'^\[([A-Za-z0-9_]+)\s+"(.*)"\]$')
MOVE_NUMBER_RE = re.compile(r"^\d+\.(?:\.\.)?$")
RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}


class LocalFullGameError(RuntimeError):
    pass


def parse_uci_pgn(path: Path | str) -> list[dict[str, Any]]:
    """Parse Fastchess PGN written with notation=uci.

    This is deliberately not a chess-legality parser; Fastchess is the rules
    authority. It is a strict serialization parser used to reconstruct the
    exact move history sent to Allfather.
    """
    source=Path(path)
    text=source.read_text(encoding="utf-8")
    starts=[match.start() for match in re.finditer(r"(?m)^\[Event\s", text)]
    if not starts:
        if text.strip():
            raise LocalFullGameError(f"{source}: no PGN Event records found")
        return []
    starts.append(len(text))
    games=[]
    for index in range(len(starts)-1):
        block=text[starts[index]:starts[index+1]].strip()
        lines=block.splitlines()
        headers:dict[str,str]={}
        body_lines=[]
        in_body=False
        for line in lines:
            if not in_body and line.startswith("["):
                match=HEADER_RE.fullmatch(line.strip())
                if match is None:
                    raise LocalFullGameError(f"{source}: malformed PGN header {line!r}")
                headers[match.group(1)]=match.group(2)
                continue
            if not line.strip() and not in_body:
                in_body=True
                continue
            in_body=True
            body_lines.append(line)
        body="\n".join(body_lines)
        body=re.sub(r"\{.*?\}", " ", body, flags=re.S)
        body=re.sub(r";[^\n]*", " ", body)
        # Fastchess does not emit variations for this campaign. Reject rather
        # than silently flattening one into the main line.
        if "(" in body or ")" in body:
            raise LocalFullGameError(f"{source}: unexpected PGN variation")
        moves=[]
        leftovers=[]
        for token in body.split():
            stripped=token.strip()
            if not stripped or stripped in RESULTS or stripped.startswith("$"):
                continue
            if MOVE_NUMBER_RE.fullmatch(stripped) or re.fullmatch(r"\d+\.\.\.", stripped):
                continue
            lowered=stripped.lower()
            if MOVE_RE.fullmatch(lowered):
                moves.append(lowered)
            else:
                leftovers.append(stripped)
        if leftovers:
            raise LocalFullGameError(
                f"{source}: notation=uci PGN contains unparsed tokens {leftovers!r}"
            )
        if "White" not in headers or "Black" not in headers or "Result" not in headers:
            raise LocalFullGameError(f"{source}: PGN is missing White/Black/Result headers")
        base_fen=headers.get("FEN", STARTPOS_FEN)
        games.append({
            "index":index,
            "headers":headers,
            "base_fen":base_fen,
            "moves":moves,
        })
    return games


def parse_proxy_transcript(path: Path | str) -> dict[str, Any]:
    """Reconstruct exact external UCI requests from one transcript JSONL."""
    source=Path(path)
    rows=[]
    for line_no,raw in enumerate(source.read_text(encoding="utf-8").splitlines(),1):
        if not raw.strip():
            continue
        try:
            row=json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LocalFullGameError(f"{source}:{line_no}: invalid JSON: {exc}") from exc
        if row.get("schema_version") != 1:
            raise LocalFullGameError(f"{source}:{line_no}: unsupported transcript schema")
        rows.append(row)

    protocol_errors=[row for row in rows if row.get("event")=="protocol_error"]
    starts=[row for row in rows if row.get("event")=="proxy_start"]
    exits=[row for row in rows if row.get("event")=="proxy_exit"]
    requests=[]
    by_key:dict[tuple[str,int],dict[str,Any]]={}
    for row in rows:
        if row.get("event")!="line":
            continue
        direction=row.get("direction")
        line=row.get("line")
        instance=str(row.get("proxy_instance") or "")
        request_index=row.get("request_index")
        if direction=="in" and isinstance(line,str) and (line=="go" or line.startswith("go ")):
            if not isinstance(request_index,int):
                raise LocalFullGameError(f"{source}: go line missing request_index")
            position=row.get("position")
            if not isinstance(position,str):
                raise LocalFullGameError(f"{source}: go line missing active position")
            key=(instance,request_index)
            if key in by_key:
                raise LocalFullGameError(f"{source}: duplicate go identity {key}")
            item={
                "proxy_instance":instance,
                "game_index":int(row.get("game_index",-1)),
                "request_index":request_index,
                "position":position,
                "go":line,
                "bestmoves":[],
                "seq":int(row.get("seq",0)),
            }
            by_key[key]=item
            requests.append(item)
        elif (
            direction=="out"
            and isinstance(line,str)
            and line.startswith("bestmove ")
            and isinstance(request_index,int)
        ):
            item=by_key.get((instance,request_index))
            if item is None:
                protocol_errors.append({
                    "event":"protocol_error",
                    "reason":"bestmove could not be associated with a go",
                    "line":line,
                    "proxy_instance":instance,
                    "request_index":request_index,
                })
            else:
                item["bestmoves"].append(line)

    requests.sort(key=lambda item:(item["proxy_instance"],item["seq"]))
    return {
        "rows":rows,
        "proxy_starts":starts,
        "proxy_exits":exits,
        "protocol_errors":protocol_errors,
        "requests":requests,
    }


def expected_arm_moves(game: dict[str,Any], display_name: str, opening_plies: int) -> list[dict[str,Any]]:
    headers=game["headers"]
    if headers["White"]==display_name:
        parity=0
    elif headers["Black"]==display_name:
        parity=1
    else:
        return []
    moves=game["moves"]
    return [
        {"ply":index, "move":move, "history":moves[:index]}
        for index,move in enumerate(moves)
        if index>=opening_plies and index%2==parity
    ]


def validate_request_position(request: dict[str,Any], *, base_fen: str, history: list[str]) -> list[str]:
    problems=[]
    try:
        position=parse_position_command(request["position"])
    except Exception as exc:
        return [f"invalid position command: {exc}"]
    if position.base_fen != base_fen:
        problems.append(
            f"base FEN mismatch: transcript={position.base_fen!r} PGN={base_fen!r}"
        )
    if list(position.moves) != list(history):
        problems.append(
            f"history mismatch: transcript={list(position.moves)!r} PGN={list(history)!r}"
        )
    return problems


def arm_outcome(headers: dict[str,str], display_name: str) -> str | None:
    if display_name not in {headers.get("White"),headers.get("Black")}:
        return None
    result=headers.get("Result")
    if result=="1/2-1/2":
        return "draw"
    if result not in {"1-0","0-1"}:
        return None
    winner="White" if result=="1-0" else "Black"
    return "win" if headers.get(winner)==display_name else "loss"


def complete_pairings(arms: list[str]) -> list[list[str]]:
    return [[arms[i],arms[j]] for i in range(len(arms)) for j in range(i+1,len(arms))]
