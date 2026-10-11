"""Compile and validate exact offline lichess-bot configuration, including defaults."""
from __future__ import annotations
import copy
import json
import os
import re
import sys
from pathlib import Path

import yaml

TOKEN = "offline-fixture-token"
APPROVED = "approved_test_bot"
LOCAL_URL = re.compile(r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})/$")
FORBIDDEN_ENV = ("LICHESS_BOT_TOKEN", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy")


def sanitized_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in FORBIDDEN_ENV:
        env.pop(name, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    return env


def assert_safety(config: dict, *, release: Path, url: str) -> None:
    if not LOCAL_URL.fullmatch(url) or not (1 <= int(LOCAL_URL.fullmatch(url).group(1)) <= 65535):
        raise ValueError("only loopback HTTP with an explicit port is permitted")
    if config["url"] != url or config["token"] != TOKEN:
        raise ValueError("API endpoint or synthetic credential modified")
    engine = config["engine"]
    if (
        engine["dir"] != str((release / "deploy/bin").resolve())
        or engine["name"] != "allfather-online"
        or engine["working_dir"] != str(release.resolve())
        or engine["protocol"] != "uci"
        or engine["ponder"] is not False
        or engine.get("uci_ponder", False) is not False
        or engine.get("uci_options", {}) != {}
        or engine.get("engine_options", {}) != {}
        or bool(engine.get("go_commands", {}))
        or bool(engine.get("homemade_options", {}))
    ):
        raise ValueError("noncanonical engine UCI launch/authority config")
    if engine["polyglot"]["enabled"] or any(
        engine["online_moves"][name]["enabled"] for name in
        ("chessdb_book", "lichess_cloud_analysis", "lichess_opening_explorer", "online_egtb")
    ) or any(engine["lichess_bot_tbs"][name]["enabled"] for name in ("syzygy", "gaviota")):
        raise ValueError("external/alternate move sources enabled")
    if engine["draw_or_resign"]["resign_enabled"] or engine["draw_or_resign"]["offer_draw_enabled"]:
        raise ValueError("bridge is attempting chess outcome authority")
    ch = config["challenge"]
    if (
        ch["concurrency"] != 1 or ch["accept_bot"] is not True or ch["only_bot"] is not True
        or ch["min_base"] != 600 or ch["max_base"] != 600
        or ch["min_increment"] != 5 or ch["max_increment"] != 5
        or ch["variants"] != ["standard"] or ch["time_controls"] != ["rapid"]
        or ch["modes"] != ["casual"] or ch["allow_list"] != [APPROVED]
        or ch.get("online_block_list", []) != []
    ):
        raise ValueError("challenge policy is not exact offline test policy")
    if config["matchmaking"]["allow_matchmaking"] is not False:
        raise ValueError("online matchmaking enabled")
    if (
        config["max_takebacks_accepted"] != 0
        or config["fake_think_time"] is not False
        or config.get("rate_limiting_delay", 0) != 0
        or any(config["greeting"].get(k) for k in ("hello", "goodbye", "hello_spectators", "goodbye_spectators"))
    ):
        raise ValueError("unauthorized bridge side effects")
    if not (release / "deploy/bin/allfather-online").is_file():
        raise ValueError("sealed launcher missing")


def generate(bridge: Path, release: Path, url: str, output: Path) -> dict:
    """Read the actual upstream default config, never copy assumed defaults."""
    base = yaml.safe_load((bridge / "config.yml.default").read_text())
    cfg = copy.deepcopy(base)
    cfg.update(token=TOKEN, url=url, fake_think_time=False, max_takebacks_accepted=0,
               move_overhead=1000, rate_limiting_delay=0, quit_after_all_games_finish=False)
    engine = cfg["engine"]
    engine.update(dir=str((release / "deploy/bin").resolve()),
                  name="allfather-online", working_dir=str(release.resolve()),
                  protocol="uci", debug=True, ponder=False,
                  engine_options={}, uci_options={})
    engine.pop("go_commands", None)
    engine["polyglot"]["enabled"] = False
    for key in ("chessdb_book", "lichess_cloud_analysis", "lichess_opening_explorer", "online_egtb"):
        engine["online_moves"][key]["enabled"] = False
    for key in ("syzygy", "gaviota"):
        engine["lichess_bot_tbs"][key]["enabled"] = False
    engine["draw_or_resign"].update(resign_enabled=False, offer_draw_enabled=False)
    cfg["greeting"] = {key: "" for key in ("hello", "goodbye", "hello_spectators", "goodbye_spectators")}
    ch = cfg["challenge"]
    ch.update(concurrency=1, accept_bot=True, only_bot=True, min_base=600,
              max_base=600, min_increment=5, max_increment=5,
              variants=["standard"], time_controls=["rapid"], modes=["casual"],
              allow_list=[APPROVED], online_block_list=[])
    cfg["matchmaking"]["allow_matchmaking"] = False
    # Apply upstream's effective defaults, then independently check their result.
    sys.path.insert(0, str(bridge.resolve()))
    try:
        from lib.config import insert_default_values, validate_config
        insert_default_values(cfg)
        assert_safety(cfg, release=release, url=url)
        validate_config(cfg)
    finally:
        sys.path.pop(0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return cfg
