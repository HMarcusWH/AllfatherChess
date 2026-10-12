"""Untracked, ignored, or modified upstream Python may never run."""
from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.lichess.fetch_bridge import copy_tracked,verify


def git(root,*args):
    return subprocess.check_output(["git","-C",str(root),*args],
                                   stderr=subprocess.PIPE,text=True).strip()


class BridgeExportTests(unittest.TestCase):
    def test_unknown_files_never_enter_tracked_source_export(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"upstream"
            root.mkdir()
            git(root,"init","--quiet")
            (root/"safe.py").write_text("VALUE=1\n")
            (root/".gitignore").write_text("ignored.py\n")
            git(root,"add","safe.py",".gitignore")
            git(root,"-c","user.email=test@example.org","-c","user.name=Test",
                "commit","--quiet","-m","frozen")
            git(root,"remote","add","origin","https://example.invalid/fixture.git")
            lock={"commit":git(root,"rev-parse","HEAD"),
                  "origin":"https://example.invalid/fixture.git",
                  "tracked_blobs":{"safe.py":git(root,"hash-object","safe.py")},
                  "scope":"ONLINE-3B-OFFLINE-ONLY","live_api_permitted":False}
            verify(root,lock)
            (root/"ignored.py").write_text("SHADOW=1\n")
            (root/"injected.py").write_text("SHADOW=2\n")
            with self.assertRaisesRegex(ValueError,"untracked, or ignored"):
                verify(root,lock)
            dest=Path(d)/"export"
            copy_tracked(root,dest,verify_lock=False)
            self.assertTrue((dest/"safe.py").is_file())
            self.assertFalse((dest/"ignored.py").exists())
            self.assertFalse((dest/"injected.py").exists())
            (root/"safe.py").write_text("VALUE=999\n")
            with self.assertRaisesRegex(ValueError,"modified"):
                verify(root,lock)


if __name__=="__main__":
    unittest.main()
