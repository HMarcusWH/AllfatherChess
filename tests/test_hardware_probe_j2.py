#!/usr/bin/env python3
"""Integration smoke for legacy hardware probes backed by J2 host evidence."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HardwareProbeJ2Tests(unittest.TestCase):
    def _probe(self, script: str) -> dict:
        raw = subprocess.check_output(
            [sys.executable, str(ROOT / "scripts" / script)],
            cwd=ROOT,
            text=True,
        )
        data = json.loads(raw)
        self.assertIsInstance(data, dict)
        return data

    def _assert_j2_binding(self, data: dict) -> None:
        host = data.get("host_capabilities")
        self.assertIsInstance(host, dict)
        self.assertEqual(host.get("authority"), {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        })
        capability_id = data.get("host_capability_id")
        self.assertIsInstance(capability_id, str)
        self.assertTrue(capability_id.startswith("host-cap/"))

        domain_id = data.get("host_qualification_domain_id")
        domain_digest = data.get("host_qualification_domain_digest")
        if domain_id is None:
            self.assertIsNone(domain_digest)
        else:
            self.assertRegex(domain_id, r"^host-domain/[0-9a-f]{20}$")
            self.assertRegex(domain_digest, r"^[0-9a-f]{64}$")

        measurement = data.get("resource_measurement")
        self.assertIsInstance(measurement, dict)
        ticks = measurement.get("clock_ticks_per_second")
        self.assertIs(type(ticks), int)
        self.assertGreater(ticks, 0)

        substrate = data.get("runtime_substrate")
        self.assertIsInstance(substrate, dict)
        self.assertEqual(substrate.get("authority"), {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        })
        substrate_id = data.get("runtime_substrate_id")
        substrate_digest = data.get("runtime_substrate_digest")
        self.assertRegex(
            substrate_id, r"^runtime-substrate/[0-9a-f]{20}$"
        )
        self.assertRegex(substrate_digest, r"^[0-9a-f]{64}$")

        commit = data.get("commit_sha")
        self.assertIsInstance(commit, str)
        self.assertRegex(commit, r"^[0-9a-f]{40}$")

    def test_lc0_probe_embeds_j2_host_evidence(self):
        self._assert_j2_binding(self._probe("lc0-hardware-probe.py"))

    def test_online_probe_embeds_j2_host_evidence(self):
        self._assert_j2_binding(self._probe("online-hardware-probe.py"))


if __name__ == "__main__":
    unittest.main()
