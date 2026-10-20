#!/usr/bin/env python3
"""Reconstruct Ubuntu signed snapshot -> Packages index -> exact native .deb chain.

Runs from an exact-source CI worktree with actual pinned Ubuntu keyring
extracted from the release image. No positive public/distribution authority.
"""
from __future__ import annotations
import argparse,hashlib,json,lzma,re,subprocess,tempfile,time,urllib.request,urllib.error
from pathlib import Path

SNAP="20261001T000000Z"
BASE=f"https://snapshot.ubuntu.com/ubuntu/{SNAP}"
SUITES=("noble","noble-updates","noble-backports","noble-security")
# A deterministic proof needs at least one authenticated matching archive
# record, not every mirror/pocket that happens to publish identical bytes.
INDEX_SCAN_ORDER=("noble-updates","noble","noble-security","noble-backports")
COMPONENTS=("main","universe")
FINGERPRINT="F6ECB3762474EDA9D21B7022871920D1991BC93C"
HEX=re.compile(r"[0-9a-f]{64}\Z")
INDEX_PATH=re.compile(r"[A-Za-z0-9._+/-]+\Z")
MAX_RELEASE=4*1024*1024
MAX_INDEX=40*1024*1024
MAX_PLAIN=200*1024*1024
def check(ok,msg):
    if not ok: raise ValueError(msg)
def digest(blob):return hashlib.sha256(blob).hexdigest()
def get(uri,limit):
    check(uri.startswith(BASE+"/dists/") and len(uri)<220,"non-snapshot download URL")
    request=urllib.request.Request(uri,headers={"User-Agent":"AllfatherChess-native-provenance/2B2"})
    for attempt in range(1,6):
        try:
            with urllib.request.urlopen(request,timeout=30) as f:
                observed=f.geturl()
                check(observed==uri,"unapproved signed-index HTTP redirect")
                data=f.read(limit+1)
            check(0<len(data)<=limit,"empty/oversize signed Ubuntu index")
            return data
        except urllib.error.HTTPError as e:
            # Only bounded, explicitly transient acquisition responses retry.
            # Signature/hash/missing-index and 500 errors remain immediate stops.
            if e.code not in (429,502,503,504) or attempt==5:
                label=uri.split("/dists/",1)[-1][:130]
                raise ValueError(
                    f"signed Ubuntu snapshot {label} HTTP {e.code} "
                    f"refused after {attempt} bounded attempt(s)") from e
            time.sleep(min(attempt*4,12))
        except (TimeoutError, ConnectionResetError) as exc:
            # Loss of transport bytes is never evidence for a native source.
            # Retry only bounded socket read/reset events; never GPG, TLS
            # certificate, index digest, redirect, or package mismatches.
            if attempt==5:
                label=uri.split("/dists/",1)[-1][:130]
                raise ValueError(
                    f"signed Ubuntu snapshot {label} transport timeout "
                    f"refused after {attempt} bounded attempt(s)") from exc
            time.sleep(min(attempt*4,12))
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason,(TimeoutError,ConnectionResetError)):
                raise ValueError("signed Ubuntu snapshot transport identity failure") from exc
            if attempt==5:
                label=uri.split("/dists/",1)[-1][:130]
                raise ValueError(
                    f"signed Ubuntu snapshot {label} transport timeout "
                    f"refused after {attempt} bounded attempt(s)") from exc
            time.sleep(min(attempt*4,12))
    raise ValueError("snapshot acquisition attempts exhausted")
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
              fields[1].isdecimal() and int(fields[1])>=0 and
              0<len(fields[2])<=300 and not fields[2].startswith("/") and
              "\\\\" not in fields[2] and
              all(33<=ord(c)<=126 for c in fields[2]) and
              ".." not in fields[2].split("/"),
              "malformed/unsafe signed index entry: " + repr(line.strip()[:135]))
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
    # Ubuntu may publish a multi-stream XZ index. A single
    # LZMADecompressor stops after one XZ stream and leaves the next stream
    # (or legal stream padding) in unused_data. Accept both only after the
    # compressed bytes have already matched the signed Release SHA256.
    check(0<len(raw)<=MAX_INDEX and raw.startswith(b"\xfd7zXZ\x00"),
          "invalid xz Packages index")
    rest=raw
    chunks=[]
    total=0
    streams=0
    while rest:
        # XZ stream padding must be four-byte aligned, and strictly zero.
        if rest[0]==0:
            pad=len(rest)-len(rest.lstrip(b"\x00"))
            check(pad%4==0,"misaligned xz stream padding")
            rest=rest[pad:]
            continue
        check(rest.startswith(b"\xfd7zXZ\x00") and streams<16,
              "non-XZ trailing data or unbounded concatenated streams")
        dec=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,
                                 memlimit=384*1024*1024)
        part=dec.decompress(rest,max_length=MAX_PLAIN-total+1)
        check(dec.eof and total+len(part)<=MAX_PLAIN,
              "truncated or oversized signed Packages.xz stream")
        chunks.append(part)
        total+=len(part)
        streams+=1
        rest=dec.unused_data
    check(streams>=1 and total>0,"signed Packages.xz empty")
    return b"".join(chunks)
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
    unavailable_indices=[]
    unavailable_suites=[]
    want={x.split("  ")[1].split("_dists_")[1].removesuffix("_InRelease"):x.split("  ")[0]
          for x in signed["runtime_inrelease_sha256_lines"]}
    check(set(want)==set(SUITES),"incomplete committed signed Ubuntu suites")
    found={n:[] for n in expected}
    # Do not fetch unused suites. Every admitted package still requires a
    # real, signed, hash-matched Packages index in an actually queried suite.
    for suite in INDEX_SCAN_ORDER:
        if all(found.values()):
            break
        try:
            raw=get(f"{BASE}/dists/{suite}/InRelease",MAX_RELEASE)
        except ValueError as err:
            # An unavailable suite contributes ZERO trusted evidence. Skip only
            # if another signed suite ultimately proves every package.
            if not re.search(r"(HTTP (429|500|502|503|504)|transport timeout) refused after",str(err)):
                raise
            unavailable_suites.append({"suite":suite,"reason":"snapshot_release_unavailable"})
            continue
        check(digest(raw)==want[suite],
              "signed Ubuntu release bytes differ from frozen source: "+suite)
        signatures[suite]=check_gpgv(raw,keyring)
        releases[suite]=parse_sha256_inrelease(raw)
        print(f"SIGNED_RELEASE_VERIFIED suite={suite}",flush=True)
        for component in COMPONENTS:
            if all(found.values()):
                break
            rel=f"{component}/binary-amd64/Packages.xz"
            signed_entry=releases[suite].get(rel)
            check(signed_entry is not None,"missing signed Packages.xz entry for "+suite+"/"+component)
            original_uri=f"{BASE}/dists/{suite}/{rel}"
            try:
                blob=get(original_uri,MAX_INDEX)
            except ValueError as e:
                # Only a transiently unavailable optional signed pocket may
                # be skipped. Missing mandatory package provenance still
                # fails after all permitted pocket candidates are checked.
                if not re.search(r"HTTP (429|500|502|503|504) refused after",str(e)):
                    raise
                byhash=f"{BASE}/dists/{suite}/{component}/binary-amd64/by-hash/SHA256/{signed_entry[0]}"
                try:
                    blob=get(byhash,MAX_INDEX)
                except ValueError as other:
                    if not (re.search(r"(HTTP (429|500|502|503|504)|transport timeout) refused after",str(other))
                            or "HTTP 404 refused" in str(other)):
                        raise
                    unavailable_indices.append({"suite":suite,"component":component,
                                                "index_path":rel,
                                                "index_sha256":signed_entry[0],
                                                "reason":"temporary_signed_snapshot_index_unavailable"})
                    continue
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
                if (item.get("Version")!=rec["version"] or
                    item.get("Architecture")!=rec["architecture"] or
                    item.get("SHA256")!=rec["sha256"]):
                    continue
                archive=item.get("Filename","")
                size=item.get("Size","")
                check(archive.startswith("pool/") and
                      len(archive)<=260 and
                      re.fullmatch(r"[A-Za-z0-9._+%:~/-]+",archive) and
                      ".." not in archive.split("/") and
                      archive.endswith("/"+rec["filename"]) and
                      size.isdecimal() and int(size)>0,
                      "signed package archive pool identity/size invalid for "+
                      str(name)+": "+repr(archive[:125]))
                origin={"suite":suite,"component":component,
                        "packages_index_path":rel,
                        "packages_index_sha256":signed_entry[0],
                        "packages_index_size":signed_entry[1],
                        "inrelease_sha256":scanned[-1]["inrelease_sha256"],
                        "pool_filename":archive,"archive_size":int(size)}
                if not found[name]:
                    found[name].append(origin)
            print(f"SIGNED_PACKAGE_INDEX_VERIFIED suite={suite} component={component} "
                  f"resolved={sum(bool(v) for v in found.values())}/29",flush=True)
        if all(found.values()):
            break
    missing=sorted(n for n,origins in found.items() if not origins)
    check(not missing,"29 acquired packages not linked to signed Packages metadata: "+",".join(missing))
    for n in found:
        found[n].sort(key=lambda x:(x["suite"],x["component"],x["packages_index_path"],
                                    x["pool_filename"]))
    locked=all("signed_origins" in rec for _,rec in expected.values())
    if locked:
        for name,(_,rec) in expected.items():
            check(type(rec["signed_origins"]) is list and
                  len(rec["signed_origins"])>=1 and
                  found[name][0] in rec["signed_origins"],
                  "candidate per-package signed origin does not match authenticated index: "+name)
    elif not allow_unlocked:
        raise ValueError("native per-package signed origins have not been committed")
    return {"schema_version":1,"verdict":"PASS" if locked else "CANDIDATE",
            "signed_source_provenance_verified":True,
            "exact_package_count":len(found),"index_count":len(scanned),
            "candidate_only":True,"native_lock_approved":False,
            "public_bot_release":False,
            "origin_candidates":found,"signed_index_catalogue":scanned,
            "unavailable_optional_signed_indices":unavailable_indices,
            "unavailable_unneeded_or_unproved_suites":unavailable_suites,
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
