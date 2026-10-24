"""Runtime-only operator approval: exact-source, Ed25519 signature and one-use lease.

No source policy flag can activate the gateway. Only an externally signed, short-lived
manifest that matches the measured host, source, image, reviewed license and opponents
may be consumed. The external public key is supplied as a read-only file. Never logs
the admission's signature or any credential; token files are separate.
"""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("schema_version", "source_commit", "source_tree", "image_digest",
          "policy_sha256", "host_id", "host_digest", "owner_bot", "opponent_bot",
          "session_nonce", "not_before_utc", "expires_at_utc", "max_games",
          "legal_review_digest", "operator_approval_id", "signature_b64")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
BOT = re.compile(r"^[A-Za-z0-9_-]{2,30}$")
NONCE = re.compile(r"^[0-9a-f]{32,64}$")

class AdmissionDenied(ValueError):
    pass

@dataclass(frozen=True)
class SourceBinding:
    source_commit: str
    source_tree: str
    image_digest: str
    policy_sha256: str
    host_id: str
    host_digest: str
    legal_review_digest: str
    owner_bot: str
    opponent_bot: str

def _date(value: str) -> dt.datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise AdmissionDenied("invalid UTC lease time")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise AdmissionDenied("invalid UTC lease date") from exc

def _canonical(value: dict) -> bytes:
    return (json.dumps({k: v for k, v in value.items() if k != "signature_b64"},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")

def _verify_ed25519(payload: bytes, signature: bytes, public_key: Path) -> None:
    if public_key.is_symlink() or not public_key.is_file() or public_key.stat().st_size > 8192:
        raise AdmissionDenied("operator trust anchor unavailable")
    # OpenSSL Ed25519 supports -rawin verification; require a full distinct
    # read-only public trust anchor. A source-controlled self-authored PASS is not authority.
    with tempfile.TemporaryDirectory(prefix="allfather-admission-") as d:
        raw, sig = Path(d)/"approval.json", Path(d)/"approval.sig"
        raw.write_bytes(payload)
        sig.write_bytes(signature)
        os.chmod(raw, 0o600)
        os.chmod(sig, 0o600)
        try:
            result = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key),
                 "-rawin", "-in", str(raw), "-sigfile", str(sig)],
                capture_output=True, timeout=5, check=False,
                env={"PATH":"/usr/bin:/bin","LC_ALL":"C"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdmissionDenied("operator-signature verification unavailable") from exc
        if result.returncode != 0:
            raise AdmissionDenied("operator signature invalid")

def verify_admission(lease: dict, expected: SourceBinding, public_key: Path,
                     *, now: dt.datetime | None = None) -> dict:
    if not isinstance(lease, dict) or set(lease) != set(FIELDS):
        raise AdmissionDenied("runtime admission has missing or additional fields")
    if type(lease["schema_version"]) is not int or lease["schema_version"] != 1:
        raise AdmissionDenied("unknown admission version")
    if type(lease["max_games"]) is not int or lease["max_games"] != 1:
        raise AdmissionDenied("multi-game lease denied")
    for key in ("source_commit","source_tree"):
        if not isinstance(lease[key],str) or not COMMIT.fullmatch(lease[key]):
            raise AdmissionDenied("invalid source identity")
    if not isinstance(lease["image_digest"],str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",lease["image_digest"]):
        raise AdmissionDenied("invalid image digest")
    for key in ("policy_sha256","host_digest","legal_review_digest"):
        if not isinstance(lease[key],str) or not DIGEST.fullmatch(lease[key]):
            raise AdmissionDenied("invalid input digest")
    for key in ("owner_bot","opponent_bot"):
        if not isinstance(lease[key],str) or not BOT.fullmatch(lease[key]):
            raise AdmissionDenied("invalid BOT identity")
    if lease["owner_bot"].casefold() == lease["opponent_bot"].casefold():
        raise AdmissionDenied("cannot challenge self")
    if not isinstance(lease["session_nonce"],str) or not NONCE.fullmatch(lease["session_nonce"]):
        raise AdmissionDenied("invalid nonce")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}",str(lease["host_id"])):
        raise AdmissionDenied("invalid host identifier")
    if not re.fullmatch(r"[A-Za-z0-9._-]{12,80}",str(lease["operator_approval_id"])):
        raise AdmissionDenied("missing operator review identifier")
    start,end=_date(lease["not_before_utc"]),_date(lease["expires_at_utc"])
    t=now or dt.datetime.now(dt.timezone.utc)
    if t.tzinfo is None or not start<=t<=end or not dt.timedelta(0)<end-start<=dt.timedelta(minutes=15):
        raise AdmissionDenied("lease expired, not yet active or too long")
    for name in expected.__dataclass_fields__:
        got,want=lease[name],getattr(expected,name)
        if name in ("owner_bot","opponent_bot"):
            got,want=got.casefold(),want.casefold()
        if got!=want:
            raise AdmissionDenied("runtime approval does not bind measured "+name)
    try:
        sig=base64.b64decode(lease["signature_b64"], validate=True)
    except (ValueError,TypeError) as exc:
        raise AdmissionDenied("invalid approval signature encoding") from exc
    if len(sig)!=64:
        raise AdmissionDenied("invalid Ed25519 signature length")
    _verify_ed25519(_canonical(lease),sig,public_key)
    return dict(lease)

def policy_is_denied(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise AdmissionDenied("source policy unavailable")
    data=path.read_bytes()
    policy=json.loads(data)
    deny=("outbound_challenges_allowed","automatic_matchmaking_allowed",
          "external_move_sources_allowed","chat_abort_resign_takeback_upgrade_allowed",
          "move_retries_allowed","operator_armed","production_publication_authorized",
          "live_game_authorized","dependency_lock_approved","legal_review_completed",
          "destination_host_qualified","public_bot_release")
    if (policy.get("schema_version")!=1 or policy.get("experiment_only") is not True
            or policy.get("api_origin")!="https://lichess.org"
            or policy.get("bot_account_username") is not None
            or policy.get("opponent_username") is not None
            or any(policy.get(k) is not False for k in deny)):
        raise AdmissionDenied("source policy attempted to authorize live play")
    return hashlib.sha256(data).hexdigest()

class LeaseLedger:
    """One new game in the entire durable container lifetime, including restarts."""
    def __init__(self, db: Path):
        self.db=Path(db)
        if self.db.is_symlink() or self.db.parent.is_symlink():
            raise AdmissionDenied("linked lease storage")
    def initialize(self):
        self.db.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("CREATE TABLE IF NOT EXISTS admission (singleton INTEGER PRIMARY KEY CHECK(singleton=1), nonce TEXT NOT NULL, challenge TEXT, game TEXT, terminal INTEGER NOT NULL DEFAULT 0)")
    def claim(self, nonce: str):
        with sqlite3.connect(self.db, isolation_level=None) as c:
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT nonce FROM admission WHERE singleton=1").fetchone()
            if row is None:
                c.execute("INSERT INTO admission(singleton,nonce) VALUES(1,?)",(nonce,))
            elif row[0]!=nonce:
                raise AdmissionDenied("container has already consumed another lease")
            c.execute("COMMIT")
    def bind_challenge(self, nonce: str, challenge: str):
        with sqlite3.connect(self.db, isolation_level=None) as c:
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT nonce,challenge,game,terminal FROM admission WHERE singleton=1").fetchone()
            if not row or row[0]!=nonce or row[2] or row[3] or (row[1] and row[1]!=challenge):
                raise AdmissionDenied("one-challenge admission consumed")
            c.execute("UPDATE admission SET challenge=? WHERE singleton=1",(challenge,))
            c.execute("COMMIT")
    def bind_game(self, nonce: str, game: str):
        with sqlite3.connect(self.db, isolation_level=None) as c:
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT nonce,game,terminal FROM admission WHERE singleton=1").fetchone()
            if not row or row[0]!=nonce or row[2] or (row[1] and row[1]!=game):
                raise AdmissionDenied("one-game lifetime consumed")
            c.execute("UPDATE admission SET game=? WHERE singleton=1",(game,))
            c.execute("COMMIT")
    def finish(self, nonce: str):
        with sqlite3.connect(self.db, isolation_level=None) as c:
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT nonce,game FROM admission WHERE singleton=1").fetchone()
            if not row or row[0]!=nonce or not row[1]:
                raise AdmissionDenied("terminal without bound game")
            c.execute("UPDATE admission SET terminal=1 WHERE singleton=1")
            c.execute("COMMIT")
    def snapshot(self) -> dict:
        with sqlite3.connect(self.db) as c:
            row=c.execute("SELECT nonce,challenge,game,terminal FROM admission WHERE singleton=1").fetchone()
        return dict(zip(("nonce","challenge","game","terminal"),row)) if row else {}
