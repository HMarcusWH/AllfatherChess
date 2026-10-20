#!/usr/bin/env python3
"""RELEASE-2B-2 source-frozen Ubuntu snapshot and CA probe validator.

APT's actual Signed-By authentication happens in pinned-base Docker,
not in a text parser. This code checks immutable policy + replayable signed
index and downloaded CA evidence. Candidate PASS never approves a lock.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = "qualification/release-bootstrap-v1.json"
NATIVE = "qualification/release-native-build-v1.json"
OS_LOCK = "qualification/release-os-packages.lock"
RELEASE = "qualification/release-image-v1.json"
HEX = re.compile(r"[a-f0-9]{64}\Z")
VERSION = "20260601~24.04.1"
SNAPSHOT = "20261001T000000Z"
KEYRING = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
MAX_SOURCE = 4 * 1024
MAX_EVIDENCE = 1024 * 1024


def require(ok: bool, label: str):
    if not ok:
        raise ValueError(label)


def bounded(path: Path, maximum: int = MAX_EVIDENCE) -> bytes:
    require(path.is_file() and not path.is_symlink() and
            0 < path.stat().st_size <= maximum,
            "missing/linked/oversize file: " + str(path))
    return path.read_bytes()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    result = json.loads(bounded(path))
    require(type(result) is dict, "JSON policy must be an object")
    return result


def stanzas(path: Path, *, transport: str, snapshot: str) -> list[dict]:
    content = bounded(path, MAX_SOURCE).decode("ascii")
    require("trusted=yes" not in content and
            "allow-unauthenticated" not in content and
            "Verify-Peer=false" not in content and
            "Verify-Host=false" not in content,
            "unapproved APT security bypass")
    blocks = [s for s in re.split(r"\n[ \t]*\n", content.strip())
              if any(x.strip() and not x.strip().startswith("#") for x in s.splitlines())]
    require(len(blocks) == 2, "APT sources must have exactly two stanzas")
    result = []
    expected = [
        (transport + "://archive.ubuntu.com/ubuntu", ["noble", "noble-updates"]),
        (transport + "://security.ubuntu.com/ubuntu", ["noble-security"]),
    ]
    if transport == "https":
        expected[0] = (expected[0][0], ["noble", "noble-updates", "noble-backports"])
    required = {"Types", "URIs", "Suites", "Components", "Architectures", "Signed-By", "Snapshot"}
    for stanza, (uri, suites) in zip(blocks, expected):
        fields = {}
        for raw in stanza.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            require(":" in line, "malformed APT stanza")
            key, value = line.split(":", 1)
            require(key not in fields, "duplicate APT source field")
            fields[key] = value.strip()
        require(set(fields) == required and fields["Types"] == "deb" and
                fields["URIs"] == uri and
                fields["Suites"].split() == suites and
                fields["Snapshot"] == snapshot and
                fields["Signed-By"] == KEYRING and
                fields["Architectures"] == "amd64" and
                set(fields["Components"].split()) ==
                    ({"main"} if transport == "http" else
                     {"main", "restricted", "universe", "multiverse"}),
                "floating/unsigned/wrong-snapshot APT input")
        result.append(fields)
    return result


def inventory(raw: bytes) -> dict[str, str]:
    require(raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw,
            "non-canonical native inventory encoding")
    try:
        rows = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("non-ASCII native inventory") from exc
    require(rows == sorted(set(rows)) and len(rows) >= 20,
            "native inventory must be sorted and unique")
    result = {}
    for row in rows:
        require(row.count("=") == 1 and len(row) < 260,
                "invalid native name/version record")
        name, version = row.split("=", 1)
        require(re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", name) is not None and
                re.fullmatch(r"[0-9][A-Za-z0-9.+:~_-]*", version) is not None and
                name not in result, "duplicate/bad native package/version")
        result[name] = version
    return result


def policy(root: Path = ROOT) -> tuple[dict, dict, dict]:
    b = read_json(root / BOOTSTRAP)
    n = read_json(root / NATIVE)
    lock = read_json(root / OS_LOCK)
    r = read_json(root / RELEASE)
    require(r.get("platform") == "linux/amd64" and
            r.get("apt_snapshot") == SNAPSHOT and
            r.get("ca_bootstrap_snapshot_locked") is False and
            r.get("os_package_lock_complete") is False and
            r.get("wheel_lock_complete") is False and
            r.get("public_bot_release") is False,
            "release policy attempted premature/native/public approval")
    base = r.get("base_image")
    require(isinstance(base, str) and
            re.fullmatch(r"docker\.io/library/ubuntu@sha256:[0-9a-f]{64}", base) is not None,
            "Ubuntu base must have exact index digest")
    require(b.get("schema_version") == n.get("schema_version") ==
            lock.get("schema_version") == 1 and
            b.get("scope") == "release-2b-2-candidate-signed-http-ubuntu-bootstrap" and
            n.get("scope") == "release-2b-2-native-build-candidate" and
            lock.get("scope") == "release-2b-2-observed-native-inventory-candidate",
            "unknown 2B-2 candidate lock schema")
    require(b.get("base_image") == n.get("base_image") ==
            lock.get("base_image") == base and
            b.get("apt_snapshot") == n.get("apt_snapshot") ==
            lock.get("apt_snapshot") == SNAPSHOT and
            b.get("platform") == n.get("platform") ==
            lock.get("platform") == "linux/amd64",
            "native lock input-closure drift")
    require(b.get("source_path") == n.get("bootstrap_source") ==
            r.get("ca_bootstrap_sources") == "deploy/release/bootstrap.sources" and
            n.get("runtime_source") == r.get("apt_sources") ==
            "deploy/release/ubuntu.sources" and
            n.get("native_inventory_lock") == OS_LOCK and
            n.get("bootstrap_policy") == BOOTSTRAP,
            "source role/path differs from policy")
    stanzas(root / b["source_path"], transport="http", snapshot=SNAPSHOT)
    stanzas(root / n["runtime_source"], transport="https", snapshot=SNAPSHOT)
    # A pinned foreign-image CA is a diagnostic TLS transport experiment,
    # never independent signature/provenance or production trust approval.
    transient = b.get("experimental_tls_transport_ca")
    builder = read_json(root / "qualification/release-python-builder-v1.json")
    require(type(transient) is dict and
            transient.get("reference") == builder.get("image_ref") and
            transient.get("source_path") == "/etc/ssl/certs/ca-certificates.crt" and
            transient.get("expected_source_file_sha256") ==
              "714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5" and
            transient.get("trust_source_reviewed") is False and
            transient.get("actual_release_inclusion_authorized") is False,
            "unreviewed bootstrap TLS transport authority")
    ca = b.get("candidate_ca", {})
    require(ca.get("package") == "ca-certificates" and
            ca.get("architecture") == "all" and
            ca.get("version") == VERSION,
            "CA package source/version drift")
    # Measured from a real candidate CI execution; recorded hashes are not
    # permission or substitute for independent signed index review.
    require(ca.get("deb_sha256") == "6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50" and
            ca.get("trust_keyring_sha256") == "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31" and
            ca.get("signed_packages_index_sha256") is None and
            ca.get("signed_inrelease_sha256") is None and
            b.get("observed_snapshot_inrelease_sha256") == {
                "noble": "cdb2f31d809f589719a53c6ad15f255b27569c4059542ada282aaa21b8e164b0",
                "noble-updates": "d640d63e6299600d21a60091db91fb11407c942ebf38ea408a1718bbaf156d80",
                "noble-security": "15e060b23470663563fc5a1dafc7797a71e4446ab587656ba7937be89564cca8",
            }, "candidate observed Ubuntu native hashes differ")
    require(all(b.get(x) is False for x in (
        "bootstrap_snapshot_proved", "bootstrap_hash_reviewed",
        "source_acquisition_signatures_independently_verified",
        "public_bot_release", "artifact_distribution_authorized",
        "offline_candidate_qualified")), "candidate bootstrap falsely promoted")
    require(all(n.get(x) is False for x in (
        "signed_index_acquisition_reviewed", "acquired_package_hashes_reviewed",
        "base_inventory_reviewed", "lock_approved", "public_bot_release",
        "artifact_distribution_authorized", "offline_candidate_qualified")),
        "native policy falsely promoted")
    rows = lock.get("expected_final_inventory")
    require(type(rows) is list and len(rows) == 120 and
            all(type(x) is str for x in rows), "observed native inventory rows missing")
    candidate = ("\n".join(rows) + "\n").encode("ascii")
    require(len(inventory(candidate)) == 120 and
            digest(candidate) == lock.get("expected_final_inventory_sha256") ==
            n.get("expected_final_inventory_sha256") ==
            "859bca91d5aa9d152424b9d839641fa942a06e55e4af8b5c50c8d7e6258c4f5a",
            "historical exact final native inventory drift")
    # Historical final (120), exact base (92), and post-bootstrap (94)
    # observations are distinct. The acquired package *hash* closure remains
    # separate and must not be inferred from these package/version records.
    require(type(lock.get("base_inventory")) is list and
            type(lock.get("observed_bootstrap_inventory")) is list and
            lock.get("base_inventory_measured") is True and
            lock.get("base_inventory_reviewed") is False and
            lock.get("acquired_package_debs") is None and
            lock.get("signed_index_evidence") is None and
            lock.get("acquired_package_hashes_measured") is False and
            lock.get("candidate_only") is True and
            lock.get("approved") is False and
            lock.get("public_bot_release") is False,
            "candidate base/acquisition origin silently promoted")
    require(len(inventory(("\n".join(lock["base_inventory"])+"\n").encode("ascii"))) == 92 and
            len(inventory(("\n".join(lock["observed_bootstrap_inventory"])+"\n").encode("ascii"))) == 94,
            "base/bootstrap raw native inventories not retained exactly")
    return b, n, lock


def probe(root: Path, proof: Path) -> dict:
    b, n, lock = policy(root)
    require(proof.is_dir() and not proof.is_symlink(), "probe directory invalid")
    needed = {"base-packages.txt", "bootstrap-packages.txt",
              "keyring.sha256", "transport-ca.sha256", "inrelease-sha256.txt",
              "ca-package-index.txt", "ca-deb.sha256", "disposition.txt"}
    files = list(proof.iterdir())
    require({p.name for p in files} == needed and len(files) == len(needed),
            "bootstrap evidence must have exact known file set")
    require(all(p.is_file() and not p.is_symlink() for p in files),
            "linked or nonregular evidence")
    base_raw = bounded(proof / "base-packages.txt")
    installed_raw = bounded(proof / "bootstrap-packages.txt")
    base = inventory(base_raw)
    installed = inventory(installed_raw)
    require(installed.get("ca-certificates") == VERSION and
            installed.get("ubuntu-keyring") is not None,
            "exact pinned CA/trust package not installed")
    # The pinned base is not necessarily the final 120-row installed image:
    # identify base and acquired rows separately, without conflating them.
    require(base_raw == ("\n".join(lock["base_inventory"])+"\n").encode("ascii") and
            installed_raw == ("\n".join(lock["observed_bootstrap_inventory"])+"\n").encode("ascii"),
            "base/bootstrap native row evidence disagrees with retained observations")
    require(base != installed and len(installed) >= len(base) and
            digest(base_raw) == n.get("observed_base_inventory_sha256") and
            digest(installed_raw) == n.get("observed_bootstrap_inventory_sha256") and
            len(base) == n.get("observed_base_package_count") == 92 and
            len(installed) == n.get("observed_bootstrap_package_count") == 94,
            "signed bootstrap native inventory differs from candidate observation")
    keyring_sha = bounded(proof / "keyring.sha256").decode("ascii").strip()
    ca_hash = bounded(proof / "ca-deb.sha256").decode("ascii").strip()
    transport_ca_hash = bounded(proof / "transport-ca.sha256").decode("ascii").strip()
    require(transport_ca_hash ==
            b["experimental_tls_transport_ca"]["expected_source_file_sha256"],
            "transient TLS bundle bytes differ from exact candidate observation")
    require(keyring_sha == b["candidate_ca"]["trust_keyring_sha256"] and
            ca_hash == b["candidate_ca"]["deb_sha256"],
            "CA/Ubuntu keyring bytes differ from independently observed candidate hashes")
    candidate = bounded(proof / "ca-package-index.txt").decode("ascii")
    require(re.search(r"(?m)^Package: ca-certificates$", candidate) and
            re.search(r"(?m)^Version: " + re.escape(VERSION) + r"$", candidate) and
            re.search(r"(?m)^Architecture: all$", candidate) and
            re.search(r"(?m)^SHA256: " + ca_hash + r"$", candidate),
            "signed APT package-record mismatch")
    index = bounded(proof / "inrelease-sha256.txt").decode("ascii").splitlines()
    require(len(index) >= 3 and len(index) <= 5 and index == sorted(set(index)),
            "missing, duplicated or unbounded InRelease identity")
    for row in index:
        m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", row)
        require(m is not None and "20261001T000000Z" in m.group(2) and
                m.group(2).endswith("_InRelease") and
                "snapshot.ubuntu.com" in m.group(2),
                "non-snapshot or unqualified signed APT index")
    expected_index_hashes = set(b["observed_snapshot_inrelease_sha256"].values())
    require({x[:64] for x in index} == expected_index_hashes,
            "signed snapshot InRelease bytes differ from observed candidate")
    require(bounded(proof / "disposition.txt") ==
            b"SIGNED_APT_SNAPSHOT_PROBE_COMPLETE\n",
            "real signed package verification did not complete")
    return {"schema_version":1, "verdict":"PASS",
            "scope":"candidate-signed-apt-bootstrap-observation-only",
            "base_inventory_sha256":digest(base_raw),
            "bootstrap_inventory_sha256":digest(installed_raw),
            "base_package_count":len(base),
            "bootstrap_package_count":len(installed),
            "ca_package_version":VERSION, "ca_deb_sha256":ca_hash,
            "trust_keyring_sha256":keyring_sha,
            "transport_ca_sha256":transport_ca_hash,
            "inrelease_sha256_lines":index,
            "apt_authentication_performed_by_pinned_docker_build":True,
            "independent_signature_attestation":False,
            "dependency_lock_approved":False,
            "public_bot_release":False,
            "artifact_distribution_authorized":False}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,default=ROOT)
    ap.add_argument("--probe-dir",type=Path)
    ap.add_argument("--output",type=Path)
    args=ap.parse_args()
    try:
        if args.probe_dir is None:
            _,_,lock=policy(args.root)
            report={"schema_version":1,"verdict":"PASS",
                    "scope":"candidate-snapshot-source-policy-only",
                    "final_candidate_package_count":len(lock["expected_final_inventory"]),
                    "dependency_lock_approved":False,"public_bot_release":False}
        else:
            report=probe(args.root,args.probe_dir)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
        print(json.dumps(report,sort_keys=True))
    except (OSError,ValueError,TypeError,KeyError,UnicodeError) as exc:
        ap.exit(2,"RELEASE-2B-2 signed native candidate refused: "+
                type(exc).__name__+": "+str(exc)+"\n")


if __name__=="__main__":
    main()
