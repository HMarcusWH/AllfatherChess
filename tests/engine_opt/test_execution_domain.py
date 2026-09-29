#!/usr/bin/env python3
"""Tests for execution-domain aggregation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.domain import (
    ExecutionDomainError,
    require_same_execution_domain,
)


def domain(host: str = "host-domain/a", runtime: str = "runtime-substrate/a"):
    return {
        "complete": True,
        "host_capability_id": "host-cap/abc",
        "host_qualification_domain_id": host,
        "runtime_substrate_id": runtime,
    }


class ExecutionDomainTests(unittest.TestCase):
    def test_same_domain_passes(self):
        result = require_same_execution_domain(
            {"matrix": domain(), "hybrid": domain(), "local1": domain()}
        )
        self.assertEqual(
            result["host_qualification_domain_id"],
            "host-domain/a",
        )
        self.assertEqual(result["runtime_substrate_id"], "runtime-substrate/a")

    def test_host_mismatch_fails(self):
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain(
                {"matrix": domain(), "hybrid": domain(host="host-domain/b")}
            )

    def test_runtime_mismatch_fails(self):
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain(
                {
                    "matrix": domain(),
                    "hybrid": domain(runtime="runtime-substrate/b"),
                }
            )

    def test_incomplete_or_missing_domain_fails(self):
        bad = domain()
        bad["complete"] = False
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"matrix": bad})
        with self.assertRaises(ExecutionDomainError):
            require_same_execution_domain({"matrix": None})


if __name__ == "__main__":
    unittest.main()
