from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource import LinuxProcProvider, ProcessSnapshot, ResourceProviderError


class LinuxProcParserTests(unittest.TestCase):
    def test_stat_parser_handles_spaces_inside_comm(self):
        fields = ["S"] + ["0"] * 21
        fields[11] = "12"
        fields[12] = "7"
        fields[19] = "991"
        fields[21] = "44"
        record = "123 (engine worker with spaces) " + " ".join(fields)
        self.assertEqual(
            LinuxProcProvider._parse_stat(record),
            (123, 12, 7, 991, 44),
        )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux procfs only")
    def test_zombie_terminal_snapshot_rejects_live_or_reaped_process(self):
        import time
        process=subprocess.Popen([sys.executable,"-c","pass"],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        provider=LinuxProcProvider()
        try:
            deadline=time.monotonic()+2
            terminal=None
            while time.monotonic()<deadline:
                try:
                    terminal=provider.terminal_snapshot(process.pid)
                    break
                except ResourceProviderError:
                    time.sleep(0.005)
            self.assertIsNotNone(terminal,"child did not reach waitable zombie")
            self.assertEqual(terminal.pid,process.pid)
            self.assertGreaterEqual(terminal.start_time_ticks,0)
        finally:
            process.wait(timeout=2)
            process.stdout.close()
            process.stderr.close()
        with self.assertRaises(ResourceProviderError):
            provider.terminal_snapshot(process.pid)

    def test_terminal_snapshot_refuses_non_zombie_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            pid_dir=directory/"99"
            pid_dir.mkdir()
            fields=["S"]+["0"]*21
            fields[11]="12";fields[12]="3";fields[19]="123";fields[21]="1"
            (pid_dir/"stat").write_text("99 (engine) "+" ".join(fields))
            (pid_dir/"status").write_text("VmRSS:\\t1 kB\\n")
            with self.assertRaisesRegex(ResourceProviderError,"not a waitable zombie"):
                LinuxProcProvider(proc_root=directory,clock_ticks=100).terminal_snapshot(99)

    def test_pid_reuse_is_rejected(self):
        provider = LinuxProcProvider(clock_ticks=100)
        a = ProcessSnapshot(12, 100, 1_000_000, 1, 1, 1, 1)
        b = ProcessSnapshot(12, 101, 2_000_000, 2, 2, 1, 1)
        with self.assertRaises(ResourceProviderError):
            provider.delta(a, b)

    def test_counter_regression_is_rejected(self):
        provider = LinuxProcProvider(clock_ticks=100)
        a = ProcessSnapshot(12, 100, 1_000_000, 10, 10, 1, 1)
        b = ProcessSnapshot(12, 100, 2_000_000, 9, 10, 1, 1)
        with self.assertRaises(ResourceProviderError):
            provider.delta(a, b)


class LiveProcMeasurementTests(unittest.TestCase):
    def _measure(self, mode: str) -> tuple[float, float]:
        worker = ROOT / "tests" / "fixtures" / "resource_worker.py"
        proc = subprocess.Popen(
            [sys.executable, str(worker), mode, "0.35"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdin is not None and proc.stdout is not None
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            provider = LinuxProcProvider()
            start = provider.snapshot(proc.pid)
            proc.stdin.write("run\n")
            proc.stdin.flush()
            self.assertEqual(proc.stdout.readline().strip(), "done")
            end = provider.snapshot(proc.pid)
            delta = provider.delta(start, end)
            return delta.cpu_ms, delta.wall_ms
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.write("quit\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            proc.wait(timeout=3)
            for handle in (proc.stdin, proc.stdout, proc.stderr):
                if handle is not None:
                    handle.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "procfs contract is Linux-specific")
    def test_cpu_burn_and_sleep_are_not_wall_time_in_disguise(self):
        burn_cpu, burn_wall = self._measure("burn")
        sleep_cpu, sleep_wall = self._measure("sleep")
        self.assertGreater(burn_wall, 250.0)
        self.assertGreater(sleep_wall, 250.0)
        self.assertGreater(burn_cpu, 150.0)
        self.assertLess(
            sleep_cpu,
            burn_cpu * 0.25,
            f"sleep CPU {sleep_cpu}ms looked too much like burn CPU {burn_cpu}ms",
        )


if __name__ == "__main__":
    unittest.main()
