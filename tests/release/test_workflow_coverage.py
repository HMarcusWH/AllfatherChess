"""Source-bound release workflows and runnable operator examples."""
from __future__ import annotations
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[2]


class WorkflowCoverageTests(unittest.TestCase):
    def test_release_candidate_runs_on_all_prs_and_merged_main(self):
        cfg=yaml.load((ROOT/".github/workflows/release-candidate.yml").read_text(),
                      Loader=yaml.BaseLoader)
        events=cfg["on"]
        self.assertIn("pull_request",events)
        self.assertNotIn("paths",events["pull_request"] or {})
        self.assertEqual(events["push"]["branches"],["main"])
        self.assertIn("SOURCE_SHA",cfg["env"])
        self.assertIn("github.event.pull_request.head.sha",cfg["env"]["SOURCE_SHA"])

    def test_operations_docker_command_is_valid_shell(self):
        content=(ROOT/"docs/RELEASE_1_OPERATIONS.md").read_text()
        fence=chr(96)*3
        start=content.index(fence+"sh\n",content.index("## Runtime contract"))+len(fence+"sh\n")
        end=content.index("\n"+fence,start)
        command=content[start:end]
        self.assertEqual(subprocess.run(["bash","-n"],input=command,text=True,
                                        capture_output=True).returncode,0)
        for fragment in ("--network none","--read-only","--cap-drop ALL",
                         "--security-opt no-new-privileges",
                         ":/opt/allfather/build/replays-online-hybrid-v2:rw",
                         "--user 10001:10001"):
            self.assertIn(fragment,command)


if __name__=="__main__":
    unittest.main()
