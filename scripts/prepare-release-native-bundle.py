#!/usr/bin/env python3
"""Private CI-only, source-bound Ubuntu signed dependency byte bundle.

A cache is a transport, never an authority. Every reuse verifies all raw
InRelease GPG signatures, exact source-controlled index SHA/size and all 29
native .deb SHA/size. Incomplete or invalid caches fail closed. This bundle
is NOT RELEASE-2B-3 independent-build evidence or a public distribution.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,re,shutil,subprocess,time,urllib.error,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE="https://snapshot.ubuntu.com/ubuntu/20261001T000000Z"
SUITES=("noble","noble-updates","noble-backports","noble-security")
NAME=re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")
HEX=re.compile(r"[0-9a-f]{64}\Z")
MAX_RELEASE=4*1024*1024
MAX_INDEX=40*1024*1024
MAX_DEB=100*1024*1024
def insist(ok,message):
    if not ok:raise ValueError(message)
def h(data):return hashlib.sha256(data).hexdigest()
def file(path,maxsize):
    insist(path.is_file() and not path.is_symlink() and
           0<path.stat().st_size<=maxsize,"native cache file absent/linked/oversize "+str(path))
    return path.read_bytes()
def source_module(root):
    p=root/"scripts/verify-release-native-index-chain.py"
    spec=importlib.util.spec_from_file_location("native_signed_source",p)
    insist(spec is not None and spec.loader is not None,"missing signed Ubuntu verifier source")
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
def inputs(root):
    data=json.loads((root/"qualification/release-os-packages.lock").read_text())
    boot=json.loads((root/"qualification/release-bootstrap-v1.json").read_text())
    release=json.loads((root/"qualification/release-image-v1.json").read_text())
    signed=data["signed_index_evidence"]
    insist(data["candidate_only"] is True and data["approved"] is False and
           data["public_bot_release"] is False and
           release["public_bot_release"] is False and
           release["artifact_distribution_authorized"] is False and
           release["apt_snapshot"]=="20261001T000000Z" and
           data["apt_snapshot"]==release["apt_snapshot"] and
           signed["independently_reviewed"] is False,
           "native cached bytes cannot authorize release or change snapshot")
    records={}
    suites={}
    for row in signed["runtime_inrelease_sha256_lines"]:
        digest,name=row.split("  ",1)
        suite=name.split("_dists_")[1].removesuffix("_InRelease")
        insist(suite in SUITES and HEX.fullmatch(digest) and suite not in suites,
               "invalid signed Ubuntu InRelease lock row")
        suites[suite]=digest
    insist(set(suites)==set(SUITES),"native snapshot suite set incomplete")
    for phase,field,n in (("bootstrap","bootstrap_acquired_debs",3),
                          ("runtime","acquired_package_debs",26)):
        arr=data[field]
        insist(type(arr) is list and len(arr)==n,"native archive candidate count mismatch")
        for rec in arr:
            name=rec["name"]
            insist(name not in records and NAME.fullmatch(name) and
                   len(rec.get("signed_origins",[]))==1 and
                   HEX.fullmatch(rec["sha256"]), "native signed origin candidate absent/duplicate")
            records[name]=(phase,rec,rec["signed_origins"][0])
    insist(len(records)==29,"native source bundle requires 29 unique archives")
    index_catalog={}
    for name,(phase,rec,origin) in records.items():
        suite=origin["suite"];comp=origin["component"]
        idx=origin["packages_index_path"]
        insist(suite in suites and comp in ("main","universe") and
               idx==comp+"/binary-amd64/Packages.xz" and
               origin["inrelease_sha256"]==suites[suite] and
               HEX.fullmatch(origin["packages_index_sha256"]) and
               origin["packages_index_size"]>0 and
               origin["archive_size"]>0 and
               origin["pool_filename"].startswith("pool/") and
               origin["pool_filename"].endswith("/"+rec["filename"]) and
               ".." not in origin["pool_filename"].split("/"),
               "source-lock package index/pool origin invalid: "+name)
        key=(suite,comp)
        sig=(origin["packages_index_sha256"],origin["packages_index_size"])
        if key in index_catalog:
            insist(index_catalog[key]==sig,"contradictory signed index origins")
        index_catalog[key]=sig
    insist(len(index_catalog)==4,"selected index catalogue not precisely four")
    ca=records["ca-certificates"][2]
    insist(boot["candidate_ca"]["signed_packages_index_sha256"]==ca["packages_index_sha256"] and
           boot["candidate_ca"]["signed_inrelease_sha256"]==ca["inrelease_sha256"] and
           boot["candidate_ca"]["deb_sha256"]==records["ca-certificates"][1]["sha256"],
           "candidate signed CA origin differs from source lock")
    return data,boot,suites,records,index_catalog
def fetch(url,maximum):
    insist(url.startswith(BASE+"/") and ".." not in url and len(url)<400,
           "invalid Ubuntu snapshot transport URL")
    request=urllib.request.Request(url,headers={"User-Agent":"AllfatherChess-native-input-bundle/2B2"})
    for attempt in range(1,5):
        try:
            with urllib.request.urlopen(request,timeout=30) as resp:
                insist(resp.geturl()==url,"unexpected native snapshot transport redirect")
                b=resp.read(maximum+1)
            insist(0<len(b)<=maximum,"native snapshot transport payload oversized")
            return b
        except urllib.error.HTTPError as e:
            if e.code not in (429,502,503,504) or attempt==4:
                raise ValueError("exact native snapshot transport HTTP "+str(e.code)+" refused") from e
        except (TimeoutError,ConnectionResetError) as e:
            if attempt==4:raise ValueError("exact native snapshot transport timeout") from e
        except urllib.error.URLError as e:
            if not isinstance(e.reason,(TimeoutError,ConnectionResetError)) or attempt==4:
                raise ValueError("exact native snapshot transport failed") from e
        time.sleep(min(3*attempt,9))
    raise ValueError("bounded native source acquisition exhausted")
def expected_raw(suites,records,index_catalog):
    files={}
    for suite in suites:
        files[f"dists/{suite}/InRelease"]=MAX_RELEASE
    for suite,component in index_catalog:
        files[f"dists/{suite}/{component}/binary-amd64/Packages.xz"]=MAX_INDEX
    for _,(phase,record,_) in records.items():
        files[f"archives/{phase}/{record['filename']}"]=MAX_DEB
    return files
def validate_raw(root,bundle,keyring):
    data,boot,suites,records,index_catalog=inputs(root)
    m=source_module(root)
    insist(h(file(keyring,1024*1024))==boot["candidate_ca"]["trust_keyring_sha256"],
           "raw native input uses wrong pinned Ubuntu GPG keyring")
    raw_inrelease={}
    catalogues={}
    signs={}
    for suite,sha in suites.items():
        raw=file(bundle/f"dists/{suite}/InRelease",MAX_RELEASE)
        insist(h(raw)==sha,"native bundled signed InRelease SHA mismatch: "+suite)
        signs[suite]=m.check_gpgv(raw,keyring)
        raw_inrelease[suite]=raw
        catalogues[suite]=m.parse_sha256_inrelease(raw)
    records_by_name={}
    for (suite,component),(index_sha,index_size) in index_catalog.items():
        path=f"{component}/binary-amd64/Packages.xz"
        blob=file(bundle/"dists"/suite/path,MAX_INDEX)
        insist((h(blob),len(blob))==(index_sha,index_size) and
               catalogues[suite].get(path)==(index_sha,index_size),
               "native cached Packages.xz fails signed index SHA/size")
        plain=m.decompress_index(blob)
        uncompressed=catalogues[suite].get(f"{component}/binary-amd64/Packages")
        if uncompressed is not None:
            insist((h(plain),len(plain))==uncompressed,
                   "native cached decompressed Packages index hash differs")
        for item in m.parse_debian_index(plain):
            name=item.get("Package")
            if name not in records:continue
            phase,rec,origin=records[name]
            if origin["suite"]!=suite or origin["component"]!=component:continue
            if (item.get("Version")!=rec["version"] or
                item.get("Architecture")!=rec["architecture"] or
                item.get("SHA256")!=rec["sha256"] or
                item.get("Filename")!=origin["pool_filename"] or
                item.get("Size")!=str(origin["archive_size"])):
                continue
            insist(name not in records_by_name,"duplicate matching signed Ubuntu package record")
            records_by_name[name]=item
    insist(set(records_by_name)==set(records),"signed native source fails 29/29 package origin proof")
    for name,(phase,rec,origin) in records.items():
        blob=file(bundle/"archives"/phase/rec["filename"],MAX_DEB)
        insist((h(blob),len(blob))==(rec["sha256"],origin["archive_size"]),
               "cached native .deb SHA/byte size mismatches committed signed package "+name)
    return data,boot,suites,records,index_catalog,signs,records_by_name
def expected_evidence(root,suites,records,records_by_name):
    evidence={}
    def tab(phase):
        return "".join("\t".join(rec[k] for k in ("name","version","architecture","sha256","filename"))+"\n"
                       for n,(p,rec,_) in sorted(records.items()) if p==phase)
    evidence["bootstrap/bootstrap-debs.tsv"]=tab("bootstrap").encode()
    evidence["runtime/runtime-debs.tsv"]=tab("runtime").encode()
    for suite_set,phase,filename in (
        (("noble","noble-updates","noble-security"),"bootstrap","inrelease-sha256.txt"),
        (SUITES,"runtime","runtime-inrelease-sha256.txt")):
        rows=sorted(f"{suites[x]}  snapshot.ubuntu.com_ubuntu_20261001T000000Z_dists_{x}_InRelease"
                    for x in suite_set)
        evidence[f"{phase}/{filename}"]=("\n".join(rows)+"\n").encode()
    for name,(phase,rec,origin) in records.items():
        item=records_by_name[name]
        entry=("Package: "+name+"\nVersion: "+rec["version"]+
               "\nArchitecture: "+rec["architecture"]+
               "\nFilename: "+origin["pool_filename"]+
               "\nSize: "+str(origin["archive_size"])+
               "\nSHA256: "+rec["sha256"]+"\n\n").encode("utf-8")
        evidence[f"{phase}/{phase}-index-records/{name}.txt"]=entry
        if name=="ca-certificates":
            evidence["bootstrap/ca-package-index.txt"]=entry
            evidence["bootstrap/ca-deb.sha256"]=(rec["sha256"]+"\n").encode()
    evidence["runtime/runtime-direct-selections.txt"]=(root/"qualification/release-os-apt-selections-v1.txt").read_bytes()
    evidence["runtime/source-ubuntu-declared.sources"]=(root/"deploy/release/ubuntu.sources").read_bytes()
    return evidence
def expected_files(root,bundle,suites,records,index_catalog,evidence):
    base=set(expected_raw(suites,records,index_catalog))
    base.update("evidence/"+k for k in evidence)
    base.add("bundle-identity.json")
    actual=set()
    for path in bundle.rglob("*"):
        insist(not path.is_symlink(),"linked cached Ubuntu input forbidden")
        if path.is_dir():continue
        insist(path.is_file(),"nonregular source cache input")
        actual.add(path.relative_to(bundle).as_posix())
    insist(actual==base,"unexpected/missing signed native source bundle bytes"+
           " missing="+repr(sorted(base-actual)[:5])+" extra="+repr(sorted(actual-base)[:5]))
def verify(root,bundle,keyring):
    root=root.resolve()
    insist(bundle.is_dir() and not bundle.is_symlink(),"native byte bundle absent")
    data,boot,suites,records,indices,signs,selected=validate_raw(root,bundle,keyring)
    evidence=expected_evidence(root,suites,records,selected)
    expected_files(root,bundle,suites,records,indices,evidence)
    for path,raw in evidence.items():
        insist(file(bundle/"evidence"/path,1024*1024)==raw,
               "cached native package evidence bytes disagree with verified source")
    manifest=json.loads(file(bundle/"bundle-identity.json",1024*1024))
    insist(manifest.get("schema_version")==1 and
           manifest.get("scope")=="private-ci-input-only" and
           manifest.get("candidate_only") is True and
           manifest.get("public_bot_release") is False and
           manifest.get("approved") is False and
           manifest.get("signed_source_reverified") is True and
           manifest.get("apt_snapshot")=="20261001T000000Z" and
           manifest.get("source_lock_sha256")==h((root/"qualification/release-os-packages.lock").read_bytes()) and
           manifest.get("bootstrap_policy_sha256")==h((root/"qualification/release-bootstrap-v1.json").read_bytes()),
           "native cache manifest authority or source closure mismatch")
    paths=expected_raw(suites,records,indices)
    hashes={p:h(file(bundle/p,limit)) for p,limit in paths.items()}
    insist(manifest.get("raw_input_sha256")==hashes,"cached source byte map mutated")
    return {"schema_version":1,"verdict":"PASS","scope":"private-signed-native-input-cache",
            "source_lock_sha256":manifest["source_lock_sha256"],
            "verified_native_archives":len(records),"verified_signed_releases":len(suites),
            "verified_signed_package_indexes":len(indices),
            "cache_untrusted_until_reverified":True,
            "candidate_only":True,"public_bot_release":False,"approved":False}
def populate(root,bundle,keyring):
    insist(not bundle.exists(),"existing native cache cannot be silently overwritten")
    data,boot,suites,records,indices=inputs(root)
    staging=bundle.parent/(bundle.name+".pending")
    insist(not staging.exists(),"incomplete staged native bundle must be inspected/removed explicitly")
    staging.mkdir(parents=True)
    try:
        rawfiles=expected_raw(suites,records,indices)
        for relative,limit in sorted(rawfiles.items()):
            if relative.startswith("archives/"):
                phase=relative.split("/",2)[1]
                filename=relative.split("/",2)[2]
                rec=next(rec for _,(p,rec,_) in records.items()
                         if p==phase and rec["filename"]==filename)
                origin=rec["signed_origins"][0]
                source=origin["pool_filename"]
            else:
                source=relative
            blob=fetch(BASE+"/"+source,limit)
            path=staging/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(blob)
            print("CANDIDATE_NATIVE_BYTE_ACQUIRED "+relative,flush=True)
        _,_,_,_,_,signs,selected=validate_raw(root,staging,keyring)
        evidence=expected_evidence(root,suites,records,selected)
        for rel,raw in evidence.items():
            path=staging/"evidence"/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
        raw_hashes={p:h(file(staging/p,limit)) for p,limit in rawfiles.items()}
        identity={"schema_version":1,"scope":"private-ci-input-only","candidate_only":True,
                  "public_bot_release":False,"approved":False,
                  "signed_source_reverified":True,"apt_snapshot":"20261001T000000Z",
                  "source_lock_sha256":h((root/"qualification/release-os-packages.lock").read_bytes()),
                  "bootstrap_policy_sha256":h((root/"qualification/release-bootstrap-v1.json").read_bytes()),
                  "raw_input_sha256":raw_hashes}
        (staging/"bundle-identity.json").write_text(json.dumps(identity,indent=2,sort_keys=True)+"\n")
        verify(root,staging,keyring)
        staging.rename(bundle)
    finally:
        if staging.exists():shutil.rmtree(staging)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=ROOT)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--keyring",type=Path,required=True)
    p.add_argument("--populate-if-missing",action="store_true")
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    a.root=a.root.resolve()
    if not a.bundle.exists() and a.populate_if_missing:
        a.bundle.parent.mkdir(parents=True,exist_ok=True)
        populate(a.root,a.bundle,a.keyring)
    result=verify(a.root,a.bundle,a.keyring)
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps(result,sort_keys=True))
if __name__=="__main__":main()
