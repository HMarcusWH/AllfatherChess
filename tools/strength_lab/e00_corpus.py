"""E00 source-frozen 512-history corpus; generated with pinned chess==1.11.2.

The committed recipe is the frozen input. Generated JSONL is reproducible and
sealed by its SHA256; it is not a previously run or strength-qualified campaign.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import random
import subprocess

ROOT=Path(__file__).resolve().parents[2]
RECIPE=ROOT/"tests/fixtures/strength_e00/corpus-recipe-v1.json"
POLICY=ROOT/"qualification/strength/e00-native-equivalence-v1.json"

def _hash(data:bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _chess():
    import chess
    import chess.pgn
    if chess.__version__!="1.11.2":
        raise ValueError("E00 requires pinned chess==1.11.2")
    return chess

def freeze_inputs(recipe:dict) -> None:
    for rel,blob in recipe["source_blobs"].items():
        p=ROOT/rel
        if not p.is_file() or p.is_symlink():
            raise ValueError("missing/unsafe E00 source "+rel)
        found=subprocess.check_output(["git","-C",str(ROOT),"hash-object","--",rel],text=True).strip()
        if found!=blob:
            raise ValueError("E00 source blob changed: "+rel)

def build_corpus() -> list[dict]:
    chess=_chess()
    recipe=json.loads(RECIPE.read_text(encoding="utf-8"))
    policy=json.loads(POLICY.read_text(encoding="utf-8"))
    if recipe["schema_version"]!=1 or recipe["id"]!="e00-histories-v1" or policy["corpus_recipe"]!=str(RECIPE.relative_to(ROOT)):
        raise ValueError("unsupported E00 frozen corpus recipe")
    freeze_inputs(recipe)
    seeds=[]
    pgn=(ROOT/"tests/fixtures/meta1/openings-v1.pgn").read_text(encoding="utf-8")
    with io.StringIO(pgn) as stream:
        while (game:=chess.pgn.read_game(stream)) is not None:
            if game.errors:
                raise ValueError("invalid source opening PGN")
            moves=[m.uci() for m in game.mainline_moves()]
            board=game.end().board()
            if not board.is_valid() or not list(board.legal_moves):
                raise ValueError("invalid opening base")
            seeds.append(("opening",chess.STARTING_FEN,moves))
    if len(seeds)!=50:
        raise ValueError("E00 source no longer has exactly 50 openings")
    epd=[]
    for line in (ROOT/"tests/fixtures/engine_opt/positions.epd").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        label,fen,_=[x.strip() for x in line.split("|")]
        board=chess.Board(fen)
        if not board.is_valid() or not list(board.legal_moves):
            raise ValueError("invalid frozen EPD "+label)
        epd.append((label,fen,[]))
    if len(epd)!=8:
        raise ValueError("E00 source no longer has eight EPDs")
    special=json.loads((ROOT/"tests/fixtures/lichess/legal_histories.json").read_text(encoding="utf-8"))
    if set(special)!={"startpos_castle","startpos_en_passant","startpos_double_promotion"}:
        raise ValueError("unknown special histories")
    special_seeds=[]
    for key,moves in sorted(special.items()):
        board=chess.Board()
        for item in moves:
            move=chess.Move.from_uci(item)
            if move not in board.legal_moves:
                raise ValueError("invalid source special move "+key+":"+item)
            board.push(move)
        special_seeds.append((key,chess.STARTING_FEN,moves))
    rows=[]
    seen=set()
    total=recipe["count"]
    if type(total) is not int or total!=512 or policy["sample_count"]!=total:
        raise ValueError("E00 corpus size/contract drift")
    for i in range(total):
        kind=i%4
        if kind in (0,1):
            source_label,base_fen,history=seeds[(i*37//4)%len(seeds)]
            category="opening"
        elif kind==2:
            source_label,base_fen,history=epd[(i//4)%len(epd)]
            category="epd"
        else:
            source_label,base_fen,history=special_seeds[(i//4)%len(special_seeds)]
            category="special_history"
        moves=list(history)
        board=chess.Board(base_fen)
        for text in moves:
            move=chess.Move.from_uci(text)
            if move not in board.legal_moves:
                raise ValueError("source continuation became illegal")
            board.push(move)
        r=random.Random(recipe["seed"]+i*65537)
        for ply in range(6+(i%19)):
            legal=sorted(board.legal_moves,key=lambda m:m.uci())
            if not legal:
                break
            captures=[m for m in legal if board.is_capture(m) or m.promotion]
            choices=captures if (ply%7==0 and captures) else legal
            move=choices[r.randrange(len(choices))]
            board.push(move)
            moves.append(move.uci())
            if board.is_game_over(claim_draw=True):
                board.pop()
                moves.pop()
                break
        if not board.is_valid() or not list(board.legal_moves):
            raise ValueError("E00 generated terminal position")
        prefix=("position startpos" if base_fen==chess.STARTING_FEN
                else "position fen "+base_fen)
        command=prefix+(" moves "+" ".join(moves) if moves else "")
        if command in seen:
            raise ValueError("E00 generated duplicate full-history command")
        seen.add(command)
        rows.append({"schema_version":1,"id":f"e00-{i:04d}",
                     "category":category,"source_label":source_label,
                     "position_command":command,
                     "history_sha256":_hash(command.encode("utf-8")),
                     "final_fen":board.fen(en_passant="fen"),
                     "side_to_move":"w" if board.turn else "b",
                     "legal_root_count":board.legal_moves.count(),
                     "history_length":len(moves)})
    if len(rows)!=512 or len(seen)!=512:
        raise ValueError("E00 512-history closure failed")
    return rows

def bytes_for(rows:list[dict]) -> bytes:
    return "".join(json.dumps(row,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n"
                   for row in rows).encode("utf-8")

def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output",type=Path,default=ROOT/"build/test-results/strength-e00/corpus-v1.jsonl")
    ap.add_argument("--check",action="store_true")
    args=ap.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to((ROOT/"build").resolve()):
        raise ValueError("generated E00 corpus must remain under build/")
    content=bytes_for(build_corpus())
    if args.check:
        if output.read_bytes()!=content:
            raise ValueError("frozen E00 corpus reproduction differs")
    else:
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(content)
    print(json.dumps({"count":512,"corpus_sha256":_hash(content),"path":str(output),
                      "frozen_recipe":str(RECIPE.relative_to(ROOT)),
                      "scientifically_qualified":False},sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
