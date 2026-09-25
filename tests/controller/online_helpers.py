"""Real subprocess fixtures for the opt-in clock contracts (no engine builds)."""
from contextlib import contextmanager
import io
import json
import tempfile
import time
from pathlib import Path

from controller.runtime import BackendManager
from controller.uci_frontend import UciFrontend
from controller.shadow import ShadowRunCoordinator
from controller.routing import build_router
from tests.controller.test_shadow_runtime import write_shadow_config


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('condition did not become true before timeout')


def online_config(directory, *, args=None, settings=None):
    return write_shadow_config(Path(directory), mode='active', instance_args=args, extra={
        'online_time': {'enabled': True, 'max_move_ms': 600, 'network_reserve_ms': 10,
                        'prepare_budget_ms': 50, 'quiesce_budget_ms': 200, **(settings or {})},
        'budget': {'wall_ms': 2000, 'cpu_ms': 8000, 'gpu_ms': 0,
                   'controller_overhead_reserve_ms': 200},
        'routing': {'policy': 'conservative_v1', 'calibration': None,
                    'max_stages_per_owner': 1, 'stage_cpu_ms_estimate': 50,
                    'checkpoint_interval_ms': 10, 'anchor_cpu_ms_estimate': 0},
        'resource_measurement': {'enabled': True, 'provider': 'linux-procfs-v1',
                                 'require_cpu_for_claim': True, 'require_gpu_for_claim': False,
                                 'record_memory': True},
    })


@contextmanager
def shell_fixture(*, args=None, settings=None, observe=True):
    with tempfile.TemporaryDirectory() as directory:
        manager = BackendManager.from_path(online_config(directory, args=args, settings=settings))
        manager.start()
        output = io.StringIO()
        frontend = UciFrontend(manager, output=output)
        shadow = ShadowRunCoordinator(manager, router=build_router(manager.config)) if observe else None
        frontend.shadow = shadow
        try:
            frontend.handle_command('position startpos')
            yield frontend, manager, shadow, output, Path(directory)
        finally:
            frontend.handle_command('quit')
