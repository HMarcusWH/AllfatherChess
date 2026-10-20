#!/usr/bin/env python3
"""Reconstruct Ubuntu signed snapshot -> Packages index -> exact native .deb chain.

Runs from an exact-source CI worktree with actual pinned Ubuntu keyring
extracted from the release image. No positive public/distribution authority.
"""
from __future__ import annotations
import argparse,hashlib,json,lzma,re,subprocess,tempfile,urllib.request
from pathlib import Path

SNAP="20261001T000000Z"
BASE=f"https://snapshot.ubuntu.com/ubuntu/{SNAP}"
SUITES=("noble","noble-updates","noble-backports","noble-security")
COMPONENTS=("main","universe")
FINGERPRINT="F6ECB3762474EDA9D21B7022871920D1991BC93C"
HEX=re.compile(r"[0-9a-f]{64}\Z")
INDEX_PATH=re.compile(r"[a-z0-9._+/-]+\Z")
MAX_RELEASE=4*1024*1024
MAX_INDEX=40*1024*1024
MAX_PLAIN=200*1024*1024
def check(ok,msg):
    if not ok: raise ValueError(msg)
def digest(blob):return hashlib.sha256(blob).hexdigest()
def get(uri,limit):
    check(uri.startswith(BASE+"/dists/") and len(uri)<220,"non-snapshot download URL")
    request=urllib.request.Request(uri,headers={"User-Agent":"AllfatherChess-native-provenance/2B2"})
    with urllib.request.urlopen(request,timeout=30) as f:
        observed=f.geturl()
        check(observed==uri,"unapproved signed-index HTTP redirect")
        data=f.read(limit+1)
    check(0<len(data)<=limit,"empty/oversize signed Ubuntu index")
    return data
def parse_sha256_inrelease(data):
    content=data.decode("utf-8")
    check(content.startswith("-----BEGIN PGP SIGNED MESSAGE-----\n") and
          "\n-----BEGIN PGP SIGNATURE-----\n" in content,"invalid signed Release envelope")
    signed=content.split("\n-----BEGIN PGP SIGNATURE-----\n",1)[0]
    rows=signed.split("\n")
    try: start=rows.index("SHA256:")+1
    except ValueError as e: raise ValueError("signed InRelease missing SHA256 section") from e
    answer={}
    for line in rows[start:]:
        if line and not line.startswith(" "):break
        if not line.strip():continue
        fields=line.split()
        check(len(fields)==3 and HEX.fullmatch(fields[0]) and
              fields[1].isdecimal() and int(fields[1])>0 and
              INDEX_PATH.fullmatch(fields[2]) and ".." not in fields[2].split("/"),
              "malformed/unsafe signed index entry")
        filename=fields[2]
        check(filename not in answer,"duplicate signed Packages path")
        answer[filename]=(fields[0],int(fields[1]))
    check(len(answer)>=10,"truncated signed SHA256 index catalogue")
    return answer
def check_gpgv(data,keyring):
    with tempfile.TemporaryDirectory(prefix="allfather-native-gpg-") as t:
        signed=Path(t)/"InRelease";signed.write_bytes(data)
        result=subprocess.run(["gpgv","--status-fd","1","--keyring",str(keyring),
                               str(signed)],text=True,capture_output=True,
                              timeout=15,check=False)
    check(result.returncode==0,"Ubuntu signed Release rejected by gpgv")
    signatures=re.findall(r"(?m)^\[GNUPG:\] VALIDSIG ([0-9A-F]{40})",result.stdout)
    check(len(signatures)>=1 and all(x==FINGERPRINT for x in signatures),
          "unapproved signed snapshot key fingerprint")
    return signatures
def parse_debian_index(data):
    # Only a verified uncompressed Packages payload reaches here.
    check(0<len(data)<=MAX_PLAIN and data.endswith(b"\n"),"invalid Packages index bounds")
    try:txt=data.decode("utf-8")
    except UnicodeError as e:raise ValueError("Packages index is not UTF8") from e
    for stanza in re.split(r"\n\s*\n",txt):
        if not stanza.strip():continue
        item={}
        last=None
        for line in stanza.split("\n"):
            if not line:continue
            if line.startswith((" ","\t")):
                check(last is not None,"orphan Packages continuation")
                # Important provenance fields must have no multiline payload.
                if last in ("Package","Version","Architecture","Filename","Size","SHA256"):
                    raise ValueError("ambiguous Packages identity continuation")
                continue
            check(":" in line,"malformed Packages control record")
            key,value=line.split(":",1)
            check(key not in item,"duplicate Packages control key")
            item[key]=value.lstrip()
            last=key
        yield item
def decompress_index(raw):
    check(len(raw)<=MAX_INDEX and raw.startswith(b"\xfd7zXZ\x00"),"invalid xz Packages index")
    d=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=384*1024*1024)
    plain=d.decompress(raw,max_length=MAX_PLAIN+1)
    check(len(plain)<=MAX_PLAIN and d.eof and not d.unused_data,
          "truncated/trailing/oversized Packages.xz")
    return plain
def validate_receipt(root,evidence):
    policy=json.loads((root/"qualification/release-os-packages.lock").read_text())
    boot=json.loads((root/"qualification/release-bootstrap-v1.json").read_text())
    signed=policy["signed_index_evidence"]
    check(policy.get("approved") is False and
          policy.get("candidate_only") is True and
          signed.get("candidate_only") is True and
          signed.get("independently_reviewed") is False,
          "native policy asserted unapproved authority")
    expected={}
    for phase,key in (("bootstrap","bootstrap_acquired_debs"),
                      ("runtime","acquired_package_debs")):
        records=policy[key]
        raw=(evidence/(phase+"-debs.tsv")).read_text().splitlines()
        check(len(raw)==len(records),"native acquired ledger truncated")
        for record in records:
            name=record["name"]
            check(name not in expected,"duplicate native candidate across phases")
            expected[name]=(phase,record)
        actual={x.split("\t")[0]:x.split("\t") for x in raw}
        for record in records:
            a=actual.get(record["name"])
            check(a==[record[k] for k in ("name","version","architecture","sha256","filename")],
                  "native installed/deb SHA not equal to source candidate")
    for phase,entries in (
        ("bootstrap",signed["bootstrap_inrelease_sha256_lines"]),
        ("runtime",signed["runtime_inrelease_sha256_lines"])):
        check((evidence/("inrelease-sha256.txt" if phase=="bootstrap" else
                        "runtime-inrelease-sha256.txt")).read_text().splitlines()==entries,
              "signed snapshot digest evidence differs from container")
    check(json.loads((root/"qualification/release-image-v1.json").read_text())["public_bot_release"] is False,
          "external release authority forbidden")
    return expected,signed,boot
def verify(root,evidence,keyring,allow_unlocked=False):
    check(keyring.is_file() and not keyring.is_symlink(),"missing real pinned Ubuntu signing key")
    bootstrap=json.loads((root/"qualification/release-bootstrap-v1.json").read_text())
    check(digest(keyring.read_bytes())==bootstrap["candidate_ca"]["trust_keyring_sha256"],
          "pinned Ubuntu signing key bytes mismatch")
    expected,signed,_=validate_receipt(root,evidence)
    signatures={}
    releases={}
    scanned=[]
    for suite in SUITES:
        raw=get(f"{BASE}/dists/{suite}/InRelease",MAX_RELEASE)
        want={x.split("  ")[1].split("_dists_")[1].removesuffix("_InRelease"):x.split("  ")[0]
              for x in signed["runtime_inrelease_sha256_lines"]}
        check(suite in want and digest(raw)==want[suite],
              "signed Ubuntu release bytes differ from frozen source")
        signatures[suite]=check_gpgv(raw,keyring)
        releases[suite]=parse_sha256_inrelease(raw)
    found={n:[] for n in expected}
    for suite in SUITES:
        for component in COMPONENTS:
            rel=f"{component}/binary-amd64/Packages.xz"
            signed_entry=releases[suite].get(rel)
            check(signed_entry is not None,"missing signed Packages.xz entry for "+suite+"/"+component)
            blob=get(f"{BASE}/dists/{suite}/{rel}",MAX_INDEX)
            check((digest(blob),len(blob))==signed_entry,
                  "Packages.xz bytes/size differ from signed InRelease")
            plain=decompress_index(blob)
            plain_ref=releases[suite].get(f"{component}/binary-amd64/Packages")
            if plain_ref is not None:
                check((digest(plain),len(plain))==plain_ref,
                      "signed uncompressed Packages size/hash mismatch")
            scanned.append({"suite":suite,"component":component,
                            "packages_index_path":rel,
                            "packages_index_sha256":signed_entry[0],
                            "packages_index_size":signed_entry[1],
                            "inrelease_sha256":next(x for x in signed["runtime_inrelease_sha256_lines"]
                                                     if f"_dists_{suite}_" in x)[:64]})
            for item in parse_debian_index(plain):
                name=item.get("Package")
                if name not in expected:continue
                phase,rec=expected[name]
                if item.get("Version")!=rec["version"] or
                   item.get("Architecture")!=rec["architecture"] or
                   item.get("SHA256")!=rec["sha256"]:
                    continue
                archive=item.get("Filename","")
                size=item.get("Size","")
                check(archive.startswith("pool/") and
                      INDEX_PATH.fullmatch(archive) is not None and
                      archive.endswith("/"+rec["filename"]) and
                      size.isdecimal() and int(size)>0,
                      "signed package archive pool identity/size invalid")
                origin={"suite":suite,"component":component,
                        "packages_index_path":rel,
                        "packages_index_sha256":signed_entry[0],
                        "packages_index_size":signed_entry[1],
                        "inrelease_sha256":scanned[-1]["inrelease_sha256"],
                        "pool_filename":archive,"archive_size":int(size)}
                found[name].append(origin)
    missing=sorted(n for n,origins in found.items() if not origins)
    check(not missing,"29 acquired packages not linked to signed Packages metadata: "+",".join(missing))
    for n in found:
        found[n].sort(key=lambda x:(x["suite"],x["component"],x["packages_index_path"],
                                    x["pool_filename"]))
    locked=all("signed_origins" in rec for _,rec in expected.values())
    if locked:
        for name,(_,rec) in expected.items():
            check(rec["signed_origins"]==found[name],
                  "candidate per-package signed origin does not match authenticated index: "+name)
    elif not allow_unlocked:
        raise ValueError("native per-package signed origins have not been committed")
    return {"schema_version":1,"verdict":"PASS" if locked else "CANDIDATE",
            "signed_source_provenance_verified":True,
            "exact_package_count":len(found),"index_count":len(scanned),
            "candidate_only":True,"native_lock_approved":False,
            "public_bot_release":False,
            "origin_candidates":found,"signed_index_catalogue":scanned,
            "signature_fingerprints":signatures,
            "per_package_origins_source_locked":locked}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--evidence-dir",type=Path,required=True)
    p.add_argument("--keyring",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--allow-unlocked-discovery",action="store_true")
    a=p.parse_args()
    report=verify(a.root,a.evidence_dir,a.keyring,a.allow_unlocked_discovery)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"verdict":report["verdict"],
                      "exact_package_count":report["exact_package_count"],
                      "index_count":report["index_count"],
                      "origin_candidates":report["origin_candidates"],
                      "per_package_origins_source_locked":report["per_package_origins_source_locked"],
                      "native_lock_approved":False,"public_bot_release":False},sort_keys=True))
if __name__=="__main__":main()
