"""Fail-closed LC0 frozen-network fetch regression and HTTP 429 retry tests.

Never calls an actual network. Tests simulated upstream 429/503, Retry-After,
partial downloads, hash mismatches, cache reuse and bounded exhaustion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/fetch-lc0-network.py"
spec = importlib.util.spec_from_file_location("lc0_network_fetch_retry", SCRIPT)
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def http(code: int, retry_after: str | None = None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return urllib.error.HTTPError(
        "https://training.lczero.org/get_network?sha=locked", code,
        "synthetic upstream status", headers, None,
    )


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.root = Path(self.work.name)
        self.file = self.root / "download.tmp"
        self.payload = b"LC0 frozen network fixture bytes"
        self.net = {
            "filename": "791556.pb.gz",
            "sha256": hashlib.sha256(self.payload).hexdigest(),
            "expected_size_bytes": len(self.payload),
        }

    def tearDown(self):
        self.work.cleanup()

    def test_429_honors_retry_after_and_installs_exact_bytes(self):
        opener = Mock(side_effect=[http(429, "27"), io.BytesIO(self.payload)])
        sleeper = Mock()
        size, digest = fetcher._download_with_retry(
            object(), self.file, self.net, allow_candidate_size=False,
            opener=opener, sleeper=sleeper,
        )
        sleeper.assert_called_once_with(27.0)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(size, len(self.payload))
        self.assertEqual(digest, self.net["sha256"])
        self.assertEqual(self.file.read_bytes(), self.payload)

    def test_http_date_retry_after_is_supported(self):
        now = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
        date = format_datetime(now + timedelta(seconds=40), usegmt=True)
        self.assertEqual(
            fetcher._retry_after_seconds({"Retry-After": date}, now=now),
            40.0,
        )

    def test_503_and_429_exponential_retry_bounded(self):
        opener = Mock(side_effect=[
            http(503), http(429), http(429),
            io.BytesIO(self.payload),
        ])
        sleeper = Mock()
        fetcher._download_with_retry(
            object(), self.file, self.net, allow_candidate_size=False,
            opener=opener, sleeper=sleeper,
        )
        self.assertEqual([a.args[0] for a in sleeper.call_args_list],
                         [5.0, 10.0, 20.0])

    def test_exhausted_429_does_not_succeed_or_continue_forever(self):
        opener = Mock(side_effect=[
            http(429) for _ in range(fetcher.MAX_DOWNLOAD_ATTEMPTS)])
        sleeper = Mock()
        with self.assertRaises(urllib.error.HTTPError):
            fetcher._download_with_retry(
                object(), self.file, self.net, allow_candidate_size=False,
                opener=opener, sleeper=sleeper,
            )
        self.assertEqual(opener.call_count, fetcher.MAX_DOWNLOAD_ATTEMPTS)
        self.assertEqual(sleeper.call_count, fetcher.MAX_DOWNLOAD_ATTEMPTS-1)
        self.assertLess(sum(c.args[0] for c in sleeper.call_args_list),
                        fetcher.MAX_TOTAL_SLEEP_SECONDS)

    def test_unbounded_retry_after_fails_without_silent_shorter_wait(self):
        opener = Mock(side_effect=[http(429, "99999")])
        sleeper = Mock()
        with self.assertRaisesRegex(Exception, "excessive Retry-After"):
            fetcher._download_with_retry(
                object(), self.file, self.net, allow_candidate_size=False,
                opener=opener, sleeper=sleeper,
            )
        sleeper.assert_not_called()
        self.assertEqual(opener.call_count, 1)

    def test_nontransient_error_does_not_retry(self):
        opener = Mock(side_effect=[http(403)])
        sleeper = Mock()
        with self.assertRaises(urllib.error.HTTPError) as c:
            fetcher._download_with_retry(
                object(), self.file, self.net, allow_candidate_size=False,
                opener=opener, sleeper=sleeper,
            )
        self.assertEqual(c.exception.code, 403)
        sleeper.assert_not_called()

    def test_wrong_download_hash_never_installed_as_valid_network(self):
        opener = Mock(return_value=io.BytesIO(b"X" * len(self.payload)))
        sleeper = Mock()
        with self.assertRaisesRegex(Exception, "sha256 mismatch"):
            fetcher._download_with_retry(
                object(), self.file, self.net, allow_candidate_size=False,
                opener=opener, sleeper=sleeper,
            )
        self.assertEqual(opener.call_count, 1)
        sleeper.assert_not_called()

    def test_short_partial_bytes_are_not_appended_on_retry(self):
        class Half:
            def __init__(self):
                self.reads = 0
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _size=0):
                self.reads += 1
                if self.reads == 1:
                    return b"partial-corrupted-"
                raise http(429, "0")
        opener = Mock(side_effect=[Half(), io.BytesIO(self.payload)])
        sleeper = Mock()
        fetcher._download_with_retry(
            object(), self.file, self.net, allow_candidate_size=False,
            opener=opener, sleeper=sleeper,
        )
        self.assertEqual(self.file.read_bytes(), self.payload)
        self.assertEqual(opener.call_count, 2)

    def test_verified_local_cache_skips_upstream_completely(self):
        (self.root / self.net["filename"]).write_bytes(self.payload)
        with patch.object(fetcher, "load_json", return_value={"network": self.net}), \
             patch.object(fetcher, "validate_lock"), \
             patch.object(fetcher.urllib.request, "urlopen",
                          side_effect=AssertionError("should not download")):
            rc = fetcher.main(["--output-dir", str(self.root)])
        self.assertEqual(rc, 0)

    def test_corrupt_cache_replaced_only_by_verified_new_bytes(self):
        target = self.root / self.net["filename"]
        target.write_bytes(b"wrong old cache bytes")
        with patch.object(fetcher, "load_json", return_value={
                    "network": {**self.net,
                                "url": "https://training.lczero.org/get_network?sha=test"}}), \
             patch.object(fetcher, "validate_lock"), \
             patch.object(fetcher.urllib.request, "urlopen",
                          return_value=io.BytesIO(self.payload)) as opener:
            rc = fetcher.main(["--output-dir", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(target.read_bytes(), self.payload)
        opener.assert_called_once()

    def test_symlink_cache_never_trusted_or_modified(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.write_bytes(self.payload)
        (self.root / self.net["filename"]).symlink_to(elsewhere)
        with patch.object(fetcher, "load_json", return_value={"network": self.net}), \
             patch.object(fetcher, "validate_lock"), \
             patch.object(fetcher.urllib.request, "urlopen",
                          side_effect=AssertionError("should not download")):
            with self.assertRaisesRegex(Exception, "must not be a symlink"):
                fetcher.main(["--output-dir", str(self.root)])
        self.assertTrue(elsewhere.is_file())
        self.assertEqual(elsewhere.read_bytes(), self.payload)


if __name__ == "__main__":
    unittest.main()
