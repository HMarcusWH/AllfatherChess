#!/usr/bin/env python3
"""Build a locally patched, source-locked BuildKit scanner. No release grant.

Only commits from the frozen v1.11.0 Git tree, one checked patch, and vendored
Go dependencies may determine the executable. Produces an unprivileged test
binary; Docker/OCI transport and release authority are independent gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/"qualification/release-scanner-v1.json"


def require(condition: bool, message: str):
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def command(argv: list[str], *, cwd: Path|None=None,
            env: dict|None=None, timeout: int=300,
            capture: bool=True) -> str:
    p=subprocess.run(argv,cwd=cwd,env=env,timeout=timeout,
                     stdout=subprocess.PIPE if capture else None,
                     stderr=subprocess.PIPE if capture else None,
                     check=False)
    require(p.returncode==0,
            "scanner source/build operation failed: "+argv[0])
    return p.stdout.decode("utf-8","replace").strip() if capture else ""


def check_lock(policy: dict) -> None:
    require(policy.get("schema_version")==1 and
            policy.get("scope")=="offline-release-scanner-only" and
            policy.get("upstream_repository")==
                "https://github.com/docker/buildkit-syft-scanner" and
            policy.get("upstream_tag")=="v1.11.0" and
            policy.get("upstream_commit")==
                "d88056b4e5b61d0ca037340df91be47d343b4386" and
            policy.get("toolchain_version")=="go1.25.8" and
            policy.get("vendored_syft_version")=="v1.42.3" and
            policy.get("patch_path")==
                "deploy/release/scanner/target.go.patch" and
            policy.get("required_file_hash_algorithms")==["SHA1","SHA256"] and
            policy.get("reproducible_binary_required") is True and
            policy.get("reproducible_image_required") is True and
            policy.get("public_bot_release") is False and
            policy.get("artifact_distribution_authorized") is False and
            policy.get("live_credentials_authorized") is False,
            "scanner immutable source/build policy mismatch")
    digest=policy.get("patch_sha256")
    require(type(digest) is str and len(digest)==64 and
            all(c in "0123456789abcdef" for c in digest) and
            sha256(ROOT/policy["patch_path"])==digest,
            "scanner patch bytes differ from reviewed lock")


def prepare(*, output: Path) -> dict:
    policy=json.loads(LOCK.read_text(encoding="utf-8"))
    check_lock(policy)
    require(output.resolve().is_relative_to(ROOT.resolve()),
            "scanner build output must remain within checkout")
    require(not output.is_symlink(),"scanner build output cannot be a symlink")
    output.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.update({"GOPROXY":"off","GOSUMDB":"off","GOTOOLCHAIN":"local",
                "GOFLAGS":"-mod=vendor","CGO_ENABLED":"0",
                "GOOS":"linux","GOARCH":"amd64"})
    tool=command(["go","version"],env=env)
    require(tool.startswith("go version go1.25.8 "),
            "scanner requires exact Go 1.25.8 toolchain")
    patch=ROOT/policy["patch_path"]
    with tempfile.TemporaryDirectory(prefix="release1-scanner-src-") as td:
        source=Path(td)/"upstream"
        command(["git","clone","--depth=1","--single-branch",
                 "--branch",policy["upstream_tag"],
                 policy["upstream_repository"],str(source)],timeout=600)
        require(command(["git","rev-parse","HEAD"],cwd=source)==
                policy["upstream_commit"],
                "scanner upstream source SHA mismatch")
        require(command(["git","status","--porcelain"],cwd=source)=="",
                "scanner upstream checkout not clean")
        gomod=(source/"go.mod").read_text(encoding="utf-8")
        require("\ngo 1.25.8\n" in gomod and
                "github.com/anchore/syft v1.42.3" in gomod and
                (source/"vendor/modules.txt").is_file(),
                "scanner vendored dependency baseline mismatch")
        command(["git","apply","--check",str(patch)],cwd=source)
        command(["git","apply",str(patch)],cwd=source)
        require(command(["git","diff","--name-only"],cwd=source)==
                "internal/target.go",
                "scanner patch modified unapproved source")
        command(["git","diff","--check"],cwd=source)
        patched=source/"internal/target.go"
        require('WithHashers(crypto.SHA1, crypto.SHA256)' in
                patched.read_text(encoding="utf-8"),
                "scanner required hashers missing")
        epoch=command(["git","show","-s","--format=%ct","HEAD"],cwd=source)
        env["SOURCE_DATE_EPOCH"]=epoch
        ldflags=("-buildid= "+
                 "-X github.com/docker/buildkit-syft-scanner/version.Version=v1.11.0 "+
                 "-X github.com/docker/buildkit-syft-scanner/version.SyftVersion=v1.42.3")
        binary_a=output/"scanner-build-a"
        binary_b=output/"scanner-build-b"
        for dest in (binary_a,binary_b):
            command(["go","build","-trimpath","-buildvcs=false",
                     "-ldflags",ldflags,"-o",str(dest),
                     "./cmd/syft-scanner"],cwd=source,env=env,timeout=1200)
        binary_hash=sha256(binary_a)
        require(binary_hash==sha256(binary_b),
                "scanner reproducible executable bytes differ")
        if policy.get("approved_binary_sha256") is not None:
            require(binary_hash==policy["approved_binary_sha256"],
                    "scanner executable differs from reviewed binary identity")
        final=output/"syft-scanner"
        shutil.copyfile(binary_a,final)
        final.chmod(0o555)
        report={
            "schema_version":1,
            "upstream_commit":policy["upstream_commit"],
            "go_version":policy["toolchain_version"],
            "vendored_syft_version":policy["vendored_syft_version"],
            "source_patch_sha256":policy["patch_sha256"],
            "binary_sha256":binary_hash,
            "binary_reproducible":True,
            "source_locked":True,
            "source_date_epoch":int(epoch),
            "public_bot_release":False,
            "artifact_distribution_authorized":False,
        }
        (output/"scanner-source.json").write_text(
            json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        print(json.dumps(report,sort_keys=True))
        return report


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--output",type=Path,
                   default=ROOT/"build/test-results/release1-scanner")
    args=p.parse_args()
    try:
        prepare(output=args.output)
    except (OSError,ValueError,subprocess.SubprocessError,TimeoutError) as ex:
        p.exit(2,"release scanner source candidate refused: "+
               type(ex).__name__+"\n")


if __name__=="__main__":
    main()
