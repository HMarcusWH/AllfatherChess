#!/usr/bin/env python3
"""Always-present guard: green here means public release remains prohibited."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def audit(root=ROOT):
    image=json.loads((root/"qualification/release-image-v1.json").read_text())
    license=json.loads((root/"qualification/license-compliance-v1.json").read_text())
    qual=json.loads((root/"qualification/release-qualification-v1.json").read_text())
    for doc,fields in (
        (image,("allow_real_credentials","public_bot_release","network_recovery_qualified","deployment_host_qualified","playing_strength_qualified")),
        (license,("artifact_distribution_authorized","controller_distribution_license_approved","legal_review_completed")),
        (qual,("public_bot_release","artifact_distribution_authorized","release_promotion_authorized","live_transport_qualified","license_compliance_approved","authenticated_publication_authorized","destination_host_qualified")),
    ):
        for field in fields:
            if doc.get(field) is not False:
                raise ValueError("release prohibited: "+field+" must remain false")
    return {"schema_version":1,"policy_checked":True,
            "public_release_authorized":False,"artifact_distribution_authorized":False,
            "license_compliance_approved":False,"destination_host_qualified":False,
            "live_transport_qualified":False,
            "summary":"Release promotion BLOCKED by design; offline candidate CI is diagnostic."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=ROOT)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        result=audit(a.root)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
        print(json.dumps(result,sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        p.exit(2,"RELEASE-1 policy check refused: "+str(exc)+"\n")

if __name__=="__main__":
    main()
