"""Frozen one-game bridge overlay assembled from real pinned upstream defaults."""
from __future__ import annotations
import copy
import json
import os
import re
from pathlib import Path
import yaml
from deploy.lichess import bridge_policy
from deploy.lichess.live_gateway_policy import GATEWAY_URL

def generate(bridge: Path, release: Path, output: Path, *, opponent: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,30}",opponent):
        raise ValueError("approved BOT opponent is missing")
    # Offline generator is the authoritative map from pinned upstream defaults to
    # the qualified engine options. It is run unchanged against a temporary URL,
    # then the gateway-specific fields are substituted and re-audited.
    temp=output.with_suffix(".offline-tmp.yml")
    try:
        cfg=bridge_policy.generate(bridge,release,"http://127.0.0.1:17832/",temp)
    finally:
        temp.unlink(missing_ok=True)
    cfg["url"]=GATEWAY_URL
    cfg["challenge"]["allow_list"]=[opponent]
    assert_safety(cfg,release=release,opponent=opponent)
    import sys
    sys.path.insert(0,str(bridge.resolve()))
    try:
        from lib.config import insert_default_values,validate_config
        insert_default_values(cfg)
        assert_safety(cfg,release=release,opponent=opponent)
        validate_config(cfg)
    finally:
        sys.path.pop(0)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding="utf-8")
    return cfg

def assert_safety(cfg: dict, *, release: Path, opponent: str):
    if cfg.get("url")!=GATEWAY_URL or cfg.get("token")!=bridge_policy.TOKEN:
        raise ValueError("live bridge attempted direct API or actual bot token")
    clone=copy.deepcopy(cfg)
    clone["url"]="http://127.0.0.1:17832/"
    clone["challenge"]["allow_list"]=[bridge_policy.APPROVED]
    bridge_policy.assert_safety(clone,release=release,url=clone["url"])
    if cfg["challenge"]["allow_list"]!=[opponent]:
        raise ValueError("allowed BOT opponent changed")
    if cfg.get("max_takebacks_accepted")!=0 or cfg["matchmaking"]["allow_matchmaking"] is not False:
        raise ValueError("unapproved bridge side effects")
