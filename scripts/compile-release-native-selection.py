#!/usr/bin/env python3
"""Produce exact preinstall manifests from the sole candidate native lock."""
from __future__ import annotations
import argparse,hashlib,json,re
from pathlib import Path
NAME=re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")
VER=re.compile(r"[0-9][0-9A-Za-z.+:~_-]*\Z")
HEX=re.compile(r"[0-9a-f]{64}\Z")
DEB=re.compile(r"[A-Za-z0-9_.+%:~-]+\.deb\Z")
def check(ok,msg):
    if not ok: raise ValueError(msg)
def inventory(rows):
    check(type(rows) is list and rows==sorted(set(rows)) and len(rows)>20,"invalid native inventory")
    result={}
    for item in rows:
        check(type(item) is str and item.count("=")==1,"bad native inventory")
        n,v=item.split("=",1)
        check(NAME.fullmatch(n) and VER.fullmatch(v),"bad native name/version")
        result[n]=v
    return result
def compile_lock(path):
    check(path.is_file() and not path.is_symlink() and path.stat().st_size<=131072,"unavailable native source lock")
    d=json.loads(path.read_text())
    check(d.get("schema_version")==1 and d.get("candidate_only") is True and
          d.get("approved") is False and d.get("public_bot_release") is False and
          d.get("platform")=="linux/amd64" and d.get("apt_snapshot")=="20261001T000000Z",
          "unapproved native authority")
    base=inventory(d["base_inventory"]);boot=inventory(d["observed_bootstrap_inventory"])
    final=inventory(d["expected_final_inventory"])
    check((len(base),len(boot),len(final))==(92,94,120) and set(base)<=set(boot)<=set(final),
          "wrong native inventories")
    check(hashlib.sha256(("\n".join(d["expected_final_inventory"])+"\n").encode()).hexdigest()==
          d["expected_final_inventory_sha256"],"native inventory digest drift")
    output={}
    for name,old,new,field,n in (("bootstrap",base,boot,"bootstrap_acquired_debs",3),
                                ("runtime",boot,final,"acquired_package_debs",26)):
        delta={k:v for k,v in new.items() if old.get(k)!=v}
        records=d.get(field)
        check(len(delta)==n and type(records) is list and len(records)==n,"native .deb closure incomplete")
        entries=[]
        for x in records:
            check(type(x) is dict and set(x)=={"name","version","architecture","sha256","filename"},
                  "native record schema invalid")
            package=x["name"];version=x["version"];arch=x["architecture"];sha=x["sha256"];filename=x["filename"]
            check(type(package) is str and NAME.fullmatch(package) and
                  type(version) is str and VER.fullmatch(version) and delta.get(package)==version and
                  arch in ("amd64","all") and type(sha) is str and HEX.fullmatch(sha) and
                  type(filename) is str and DEB.fullmatch(filename) and
                  filename.startswith(package+"_") and filename.endswith("_"+arch+".deb"),
                  "native .deb identity/sha mismatch")
            entries.append((package,version,arch,sha,filename))
        entries.sort()
        check([e[0] for e in entries]==sorted(delta) and
              len({e[4] for e in entries})==n,"native manifest missing/duplicate .deb")
        output[name]="".join("\t".join(e)+"\n" for e in entries)
    return output
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--lock",required=True,type=Path);p.add_argument("--out",required=True,type=Path)
    a=p.parse_args();d=compile_lock(a.lock);a.out.mkdir(parents=True,exist_ok=True)
    for name,txt in d.items():
        (a.out/(name+".tsv")).write_text(txt,encoding="ascii")
        print(f"{name}: {len(txt.splitlines())} exact native .deb source candidates")
if __name__=="__main__":main()
