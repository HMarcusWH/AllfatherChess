#!/usr/bin/env python3
"""Source-only documentation inventory, local-link and authority-claim checks.

Only committed source/policies are inspected. This is NOT host, CI, release,
license, Lichess or strength qualification. No GitHub network request is made.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\((<?[^)\s>]+>?)(?:\s+[\"'][^\"']+[\"'])?\)")
BULLET = re.compile(r"^\s*-\s+\[[^\]]+\]\(([^)#]+\.md)\)", re.M)
DENIALS = {
    "qualification/current-status-v1.json": (
        "public_bot_release", "playing_strength_qualified",
        "artifact_distribution_authorized", "destination_host_qualified",
        "network_recovery_qualified", "offline_candidate_qualified",
    ),
    "qualification/release-image-v1.json": (
        "public_bot_release", "playing_strength_qualified",
        "os_package_lock_complete", "wheel_lock_complete",
        "ca_bootstrap_snapshot_locked", "network_recovery_qualified",
    ),
    "qualification/release-qualification-v1.json": (
        "public_bot_release", "offline_candidate_qualified",
        "live_transport_qualified", "artifact_distribution_authorized",
        "release_promotion_authorized",
    ),
    "qualification/license-compliance-v1.json": (
        "legal_review_completed", "controller_distribution_license_approved",
        "artifact_distribution_authorized",
    ),
    "qualification/online-live-canary-v1.json": (
        "live_game_authorized", "public_bot_release", "operator_armed",
        "legal_review_completed", "destination_host_qualified",
    ),
    "qualification/release-native-build-v1.json": ("lock_approved",),
}


class DocsDrift(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise DocsDrift(message)


def _safe_doc(root: Path, path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), f"absent/linked: {path}")
    require(path.resolve().is_relative_to(root.resolve()),
            f"document escapes source root: {path}")
    return path.read_text(encoding="utf-8")


def indexed_docs(root: Path) -> dict:
    docs_dir = root / "docs"
    index = _safe_doc(root, docs_dir / "README.md")
    actual = {p.name for p in docs_dir.glob("*.md") if p.name != "README.md"}
    entries = [Path(p).name for p in BULLET.findall(index)]
    require(len(entries) == len(set(entries)), "duplicate documentation index entries")
    require(set(entries) == actual,
            "documentation index coverage drift: missing=" +
            repr(sorted(actual-set(entries))) + ", extra=" +
            repr(sorted(set(entries)-actual)))
    return {"indexed": len(entries), "documents": len(actual)+1}


def _noncode_lines(markdown: str):
    fence = None
    for line in markdown.splitlines():
        s = line.lstrip()
        hit = re.match(r"^(`{3,}|~{3,})", s)
        if hit:
            token = hit.group(1)
            if fence is None:
                fence = token[0]
            elif fence == token[0]:
                fence = None
            continue
        if fence is None:
            yield line


def validate_local_links(root: Path) -> int:
    root = root.resolve()
    files = [root/"README.md", root/"LICENSES.md", *(root/"docs").glob("*.md")]
    checked = 0
    for source in files:
        if not source.exists():
            continue
        for line in _noncode_lines(_safe_doc(root, source)):
            for match in LINK.finditer(line):
                raw = unquote(match.group(1).strip("<>"))
                if not raw or raw.startswith("#"):
                    continue
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw) or raw.startswith("//"):
                    continue
                target = raw.split("#", 1)[0].split("?", 1)[0]
                if not target:
                    continue
                require(not target.startswith("/"),
                        f"absolute local target: {source.relative_to(root)} -> {target}")
                dest = (source.parent/target).resolve()
                require(dest.is_relative_to(root), f"link escapes repo: {source} -> {target}")
                require(dest.exists(),
                        f"broken local Markdown target: {source.relative_to(root)} -> {target}")
                checked += 1
    return checked


def check_active_status(status: str, roadmap: str, readme: str) -> None:
    # All dated historical milestone tables remain in source but are not the
    # active status/roadmap authority for new code or live release.
    active = status.split("## Historical evidence", 1)[0]
    forward = roadmap.split("## Historical roadmap text", 1)[0]
    for token in ("b4-p0-c256k-cold", "PR #64", "PR #71", "PR #76",
                  "DRY_RUN_PASS", "live_game_authorized=false",
                  "no public bot release", "META-1"):
        require(token in active, "missing active status evidence: " + token)
    for token in ("RELEASE-2B-3", "RELEASE-2B-4", "PR-B", "M15-B/C",
                  "no runtime", "not a"):
        require(token.casefold() in forward.casefold(),
                "roadmap missing open/denial boundary: " + token)
    require("25 October 2026" in active and "25 October 2026" in forward,
            "active status/roadmap snapshot date absent")
    first = "\n".join(readme.splitlines()[:16])
    require("b1ee33b19b6c" in first and "synthetic" in first.lower()
            and "no public bot release" in first.lower(),
            "root README active status is stale or promotes a release")


def check_denials(root: Path) -> int:
    checked = 0
    for relative, keys in DENIALS.items():
        path = root/relative
        data = json.loads(_safe_doc(root, path))
        require(type(data) is dict, "malformed policy: " + relative)
        for key in keys:
            require(data.get(key) is False,
                    "documentation or source changed a denied authority: " +
                    relative + ":" + key)
            checked += 1
    return checked


def audit(root: Path = ROOT) -> dict:
    root = root.resolve()
    index = indexed_docs(root)
    links = validate_local_links(root)
    status = _safe_doc(root, root/"docs/CURRENT_STATUS.md")
    roadmap = _safe_doc(root, root/"docs/ROADMAP.md")
    readme = _safe_doc(root, root/"README.md")
    check_active_status(status, roadmap, readme)
    denials = check_denials(root)
    return {"schema_version": 1, "status": "SOURCE_DOCUMENTS_CONSISTENT",
            "docs": index["documents"], "indexed": index["indexed"],
            "links_checked": links, "denial_fields_checked": denials,
            "source_claims_only": True, "live_game_authorized": False,
            "public_bot_release": False, "playing_strength_qualified": False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args()
    try:
        print(json.dumps(audit(args.root), sort_keys=True))
        return 0
    except (DocsDrift, OSError, ValueError, KeyError, TypeError) as exc:
        p.exit(2, "documentation evidence contract refused: " + type(exc).__name__ + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
