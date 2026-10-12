"""Exact-source ONLINE-4B bridge adaptation, applied ONLY to a staged copy.

Never edit the pinned upstream checkout. Every substituted source file must
match the frozen upstream Git blob ID and every replacement anchor exactly.
This adaptation only guards transport/recovery, never changes chess search.
"""
from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path

from deploy.lichess import bridge_policy

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = {
    "lib/lichess.py": "891a7715271a1f86a6f032753ca9f6f6d76c421e",
    "lib/engine_wrapper.py": "38c13f993bb4e4bf9d052f2f58e019f97030b2b0",
    "lib/lichess_bot.py": "63ab211eb433db73d1fecc47074232699dba0e94",
}
LOCAL = ("move_journal.py", "reconcile.py", "session_owner.py",
         "guarded_transport.py")


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def replace_once(s: str, before: str, after: str) -> str:
    if s.count(before) != 1:
        raise ValueError("pinned upstream patch anchor missing or ambiguous: " + before[:70])
    return s.replace(before, after, 1)


def _patch_lichess(src: str) -> str:
    src = replace_once(src, "import chess.engine\n",
                       "import chess.engine\nfrom deploy.lichess import guarded_transport as _recovery\n")
    src = replace_once(
        src, "        path_template = ENDPOINTS[endpoint_name]\n",
        "        _recovery.before_request(endpoint_name)\n"
        "        path_template = ENDPOINTS[endpoint_name]\n",
    )
    src = replace_once(
        src,
        "        response = self.session.post(url, data=data, headers=headers, params=params, json=payload, timeout=2)\n",
        "        response = _recovery.guarded_post(\n"
        "            self.session, endpoint_name, template_args, url,\n"
        "            data=data, headers=headers, params=params, payload=payload)\n",
    )
    return src


def _patch_engine(src: str) -> str:
    src = replace_once(src, "import chess\n",
                       "import chess\nfrom deploy.lichess import guarded_transport as _recovery\n")
    src = replace_once(src, "        self.add_comment(best_move, board)\n",
                       "        _recovery.stage_move(board, best_move, game.id)\n"
                       "        self.add_comment(best_move, board)\n")
    return src


def _patch_main(src: str) -> str:
    src = replace_once(src, "import chess\n",
                       "import chess\nfrom deploy.lichess import guarded_transport as _recovery\n")
    src = replace_once(
        src, "                        game.state = upd\n                        board = setup_board(game)\n",
        "                        game.state = upd\n"
        "                        _recovery.observe_state(game_id, upd)\n"
        "                        board = setup_board(game)\n",
    )
    src = replace_once(
        src, "                            engine.play_move(board,\n",
        "                            _recovery.require_ready(game.id, board)\n"
        "                            engine.play_move(board,\n",
    )
    # An actively running game on a restarted bridge must have its worker
    # re-launched. Original upstream lists active games but does not start
    # their workers until a new gameStart event is received.
    src = replace_once(
        src, "    with multiprocessing.pool.Pool(max_games + 1) as pool:\n"
             "        while not (stop.terminated or (one_game and one_game_completed) or stop.restart):\n",
        "    spawned_workers: set[str] = set()\n"
        "    with multiprocessing.pool.Pool(max_games + 1) as pool:\n"
        "        for ongoing in all_games:\n"
        "            gid = ongoing['gameId']\n"
        "            if gid in active_games and gid not in spawned_workers:\n"
        "                start_game_thread(active_games, gid,\n"
        "                                  ongoing['opponent']['username'], play_game_args, pool)\n"
        "                spawned_workers.add(gid)\n"
        "        while not (stop.terminated or (one_game and one_game_completed) or stop.restart):\n",
    )
    src = replace_once(
        src, '                active_games.pop(event["game"]["id"], None)\n'
             '                matchmaker.game_done()\n',
        src[:0] + '                active_games.pop(event["game"]["id"], None)\n'
        '                spawned_workers.discard(event["game"]["id"])\n'
        '                matchmaker.game_done()\n',
    )
    src = replace_once(
        src, '            elif event["type"] == "gameStart":\n'
             '                matchmaker.accepted_challenge(event)\n'
             '                start_game(event,\n',
        '            elif event["type"] == "gameStart":\n'
        '                matchmaker.accepted_challenge(event)\n'
        '                if event["game"]["id"] in spawned_workers:\n'
        '                    control_queue.task_done()\n'
        '                    continue\n'
        '                spawned_workers.add(event["game"]["id"])\n'
        '                start_game(event,\n',
    )
    return src


PATCHERS = {
    "lib/lichess.py": _patch_lichess,
    "lib/engine_wrapper.py": _patch_engine,
    "lib/lichess_bot.py": _patch_main,
}


def apply(staged_bridge: Path, *, manifest_path: Path | None = None) -> dict:
    staged_bridge = Path(staged_bridge).resolve()
    manifest = {
        "schema_version": 1,
        "mode": "OFFLINE-4B-ONLY",
        "upstream_commit": json.loads(
            (ROOT / "deploy/lichess/bridge.lock.json").read_text(encoding="utf-8")
        )["commit"],
        "patched_upstream": {},
        "guard_modules": {},
        "public_bot_release": False,
    }
    pending = []
    for rel, expected_git_oid in UPSTREAM.items():
        path = staged_bridge / rel
        if not path.is_file() or path.is_symlink():
            raise ValueError("upstream recovery patch target missing: " + rel)
        raw = path.read_bytes()
        if git_blob_id(raw) != expected_git_oid:
            raise ValueError("upstream recovery patch source hash drift: " + rel)
        updated = PATCHERS[rel](raw.decode("utf-8")).encode("utf-8")
        compile(updated, str(path), "exec")
        pending.append((path, updated))
        manifest["patched_upstream"][rel] = {
            "git_blob_sha1": expected_git_oid,
            "patched_sha256": hashlib.sha256(updated).hexdigest(),
        }
    # No file is modified until every frozen source and patch anchor matches.
    module_dir = staged_bridge / "deploy" / "lichess"
    if module_dir.exists():
        raise ValueError("recovery module destination already exists")
    module_dir.mkdir(parents=True)
    for name in LOCAL:
        source = ROOT / "deploy/lichess" / name
        target = module_dir / name
        shutil.copyfile(source, target)
        manifest["guard_modules"][name] = hashlib.sha256(source.read_bytes()).hexdigest()
    for path, data in pending:
        path.write_bytes(data)
    output = staged_bridge / "recovery-overlay-manifest.json"
    output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    if manifest_path is not None:
        Path(manifest_path).write_text(output.read_text(), encoding="utf-8")
    return manifest
