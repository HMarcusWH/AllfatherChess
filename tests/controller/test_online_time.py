#!/usr/bin/env python3
"""Pure clock policy, input validation, and resource/provenance contracts."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from common.search_request import parse_position_command
from controller.online_time import ClockSearch, OnlineTimeSettings, OnlineTimeError, make_time_plan, verify_time_manifest
from controller.budget import ResourceEnvelope
from controller.runtime import load_runtime_config, RuntimeError
from tests.controller.online_helpers import online_config


class TimeTests(unittest.TestCase):
    def plan(self, command='go wtime 600000 btime 600000 winc 5000 binc 5000', position='position startpos', **kwargs):
        params = dict(command=command, position=parse_position_command(position), generation=1,
            settings=OnlineTimeSettings(), envelope=ResourceEnvelope(wall_ms=12000, cpu_ms=48000),
            received_monotonic=10., controller_cpu_started_ns=100)
        params.update(kwargs)
        return make_time_plan(**params)

    def test_clock_is_capped_and_retains_original(self):
        p = self.plan()
        self.assertEqual(p.request_class, 'clock_v1')
        self.assertEqual(p.hard_budget_ms, 10000)
        self.assertLess(p.soft_deadline, p.hard_deadline)
        self.assertEqual(p.anchor_go_command, 'go movetime 9890')
        self.assertEqual(p.external_go_command, 'go wtime 600000 btime 600000 winc 5000 binc 5000')
        self.assertFalse(p.as_dict()['authority']['outward_move'])

    def test_asymmetric_clock_and_history_parity(self):
        cmd = 'go wtime 30000 btime 900 winc 2000 binc 0'
        white = self.plan(cmd)
        black = self.plan(cmd, position='position startpos moves e2e4')
        white_again = self.plan(cmd, position='position startpos moves e2e4 e7e5')
        self.assertEqual(black.side_to_move, 'b')
        self.assertEqual(black.available_clock_ms, 900)
        self.assertLess(black.hard_budget_ms, white.hard_budget_ms)
        self.assertEqual(white.hard_budget_ms, white_again.hard_budget_ms)
        p = self.plan(cmd, position='position fen 8/8/8/8/8/6k1/8/6K1 b - - 0 1')
        self.assertEqual(p.available_clock_ms, 900)

    def test_increment_is_not_spendable_before_the_move(self):
        p = self.plan('go wtime 500 btime 500 winc 1000000 binc 1000000')
        self.assertEqual(p.hard_budget_ms, 400)

    def test_fixed_movetime_includes_preparation(self):
        p = self.plan('go movetime 500 searchmoves e2e4 d2d4')
        self.assertEqual(p.hard_budget_ms, 500)
        self.assertEqual(p.anchor_go_command, 'go movetime 390 searchmoves e2e4 d2d4')
        self.assertEqual(p.request_class, 'movetime_deadline_v1')

    def test_move_horizon_and_forced_root(self):
        normal = self.plan('go wtime 30000 btime 30000')
        last = self.plan('go wtime 30000 btime 30000 movestogo 1 searchmoves e2e4')
        self.assertGreater(last.hard_budget_ms, normal.hard_budget_ms)
        self.assertTrue(last.anchor_go_command.endswith('searchmoves e2e4'))

    def test_bad_requests_are_rejected_without_a_minimum_time(self):
        for cmd in ('go', 'go infinite', 'go ponder wtime 1000 btime 1000',
                    'go wtime 1000', 'go movetime 0', 'go wtime 0 btime 1000',
                    'go movetime 1', 'go wtime 50 btime 9999 winc 999999',
                    'go wtime -1 btime 1000', 'go wtime NaN btime 1000',
                    'go wtime 1000 btime 1000 movestogo 0', 'go movetime 1000 nodes 20',
                    'go wtime 2000 btime 2000 wtime 3000', 'go movetime 500 junk',
                    'go movetime 500 searchmoves', 'go movetime 500 searchmoves E2E4',
                    'go movetime 500 searchmoves e2e4 e2e4',
                    'go movetime 500 searchmoves e2e4 searchmoves d2d4',
                    'go wtime 99999999999999999999 btime 1000'):
            with self.subTest(cmd=cmd), self.assertRaises(OnlineTimeError):
                self.plan(cmd)

    def test_invalid_settings(self):
        for field,value in [('moves_horizon',False), ('stop_grace_ms',0), ('max_move_ms',2),
                            ('increment_fraction',True), ('increment_fraction',float('nan')),
                            ('cpu_parallelism',0), ('prepare_budget_ms',.5), ('policy','bogus')]:
            with self.subTest(field=field), self.assertRaises(OnlineTimeError):
                replace(OnlineTimeSettings(), **{field:value})
        for raw in ({'enabled':1}, {'enabled':True,'typo':1}):
            with self.assertRaises(OnlineTimeError):
                OnlineTimeSettings.from_config(raw)
        self.assertIsNone(OnlineTimeSettings.from_config({'enabled':False}))

    def test_per_move_envelope_never_increases_profile(self):
        original = ResourceEnvelope(wall_ms=2000,cpu_ms=2000,
            verification_reserve_fraction=.25,controller_overhead_reserve_ms=100)
        p = self.plan('go movetime 500', envelope=original)
        self.assertEqual(p.envelope.cpu_ms, 2000)
        self.assertEqual(p.envelope.wall_ms, 500)
        self.assertEqual(p.envelope.verification_reserve_fraction,.25)
        q = self.plan('go movetime 200', envelope=original)
        self.assertEqual(q.envelope.cpu_ms,800)
        self.assertEqual(q.envelope.controller_overhead_reserve_ms,40)
        with self.assertRaises((ValueError, RuntimeError)):
            self.plan(envelope=ResourceEnvelope(wall_ms=2000,cpu_ms=2000,gpu_ms=100))

    def test_authority_commit_linearizes_against_stop_revocation(self):
        blocked_plan = self.plan(
            'go movetime 500',
            received_monotonic=time.monotonic(),
        )
        blocked = ClockSearch(blocked_plan)
        self.assertTrue(blocked.block_authority())
        self.assertFalse(blocked.commit_authority())
        self.assertFalse(blocked.authority_committed)

        committed_plan = self.plan(
            'go movetime 500',
            received_monotonic=time.monotonic(),
        )
        committed = ClockSearch(committed_plan)
        self.assertTrue(committed.commit_authority())
        self.assertTrue(committed.authority_committed)
        self.assertFalse(
            committed.block_authority(),
            "a stop after publication commit must not retroactively rewrite output",
        )

    def test_manifest_reconstructs_policy_not_only_hash(self):
        p = self.plan('go movetime 500')
        pos = parse_position_command('position startpos')
        manifest = {'generation':1, 'time_plan':p.as_dict(),
            'position':{'base_fen':pos.base_fen,'moves':[], 'variant':'standard','position_id':pos.position_id},
            'external_request':{'command':p.external_go_command},
            'stages':[{'role':'anchor','command':p.anchor_go_command,'bestmove':'e2e4'}],
            'clock_outcome':{'emitted_ms':400.,'emitted_line':'bestmove e2e4','failure':None,'output_within_deadline':True}}
        self.assertEqual(verify_time_manifest(manifest),[])
        for path,value in [(('time_plan','hard_budget_ms'),900), (('time_plan','plan_id'),'edited'),
                           (('external_request','command'),'go movetime 900'),
                           (('clock_outcome','emitted_ms'),700)]:
            bad=copy.deepcopy(manifest);bad[path[0]][path[1]]=value
            self.assertTrue(verify_time_manifest(bad))

    def test_online_timing_evidence_cannot_survive_without_time_plan(self):
        p = self.plan('go wtime 60000 btime 60000 winc 1000 binc 1000')
        pos = parse_position_command('position startpos')
        manifest = {'generation':1, 'time_plan':p.as_dict(),
            'position':{'base_fen':pos.base_fen,'moves':[], 'variant':'standard','position_id':pos.position_id},
            'external_request':{'command':p.external_go_command},
            'stages':[{'role':'anchor','command':p.anchor_go_command,'bestmove':'e2e4'}],
            'clock_outcome':{'emitted_ms':500.,'emitted_line':'bestmove e2e4','failure':None,'output_within_deadline':True}}
        missing_plan=copy.deepcopy(manifest);missing_plan.pop('time_plan')
        problems=verify_time_manifest(missing_plan)
        self.assertTrue(any('time_plan is missing' in problem for problem in problems))
        stripped=copy.deepcopy(missing_plan);stripped.pop('clock_outcome')
        problems=verify_time_manifest(stripped)
        self.assertTrue(any('time_plan is missing' in problem for problem in problems))
        legacy={'external_request':{'command':'go nodes 100'},
                'stages':[{'role':'anchor','command':'go nodes 100'}]}
        self.assertEqual(verify_time_manifest(legacy),[])
        legacy_staged={'external_request':{'command':'go movetime 800'},
                       'stages':[{'role':'anchor','command':'go nodes 20000'}]}
        self.assertEqual(verify_time_manifest(legacy_staged),[])

    def test_malformed_online_manifest_returns_integrity_errors(self):
        p = self.plan('go movetime 500')
        pos = parse_position_command('position startpos')
        manifest = {'generation':1, 'time_plan':p.as_dict(),
            'position':{'base_fen':pos.base_fen,'moves':[], 'variant':'standard','position_id':pos.position_id},
            'external_request':{'command':p.external_go_command},
            'stages':[{'role':'anchor','command':p.anchor_go_command,'bestmove':'e2e4'}],
            'clock_outcome':{'emitted_ms':400.,'emitted_line':'bestmove e2e4','failure':None,'output_within_deadline':True}}
        malformed_stages=copy.deepcopy(manifest);malformed_stages['stages']=[1]
        self.assertTrue(verify_time_manifest(malformed_stages))
        malformed_fen=copy.deepcopy(manifest)
        malformed_fen['position']['base_fen']='not a six field fen'
        bad_pos=parse_position_command('position fen not a six field fen')
        malformed_fen['position']['position_id']=bad_pos.position_id
        self.assertTrue(verify_time_manifest(malformed_fen))
        malformed_external=copy.deepcopy(manifest);malformed_external['external_request']=[]
        self.assertTrue(verify_time_manifest(malformed_external))

    def test_config_rejects_authority_gpu_and_bad_budgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = online_config(tmp)
            original=json.loads(path.read_text())
            self.assertIsNotNone(load_runtime_config(path).online_time)
            for key,value in [('wall_ms',False),('cpu_ms',0),('gpu_ms',3)]:
                bad=copy.deepcopy(original);bad['budget'][key]=value;path.write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError):load_runtime_config(path)
            bad=copy.deepcopy(original)
            bad.update({'crossfeed':{'enabled':True},'counterfactual':{'enabled':True},
                'verification':{'enabled':True},'hybrid_authority':{'enabled':True}})
            path.write_text(json.dumps(bad))
            with self.assertRaises(RuntimeError):load_runtime_config(path)

            missing=copy.deepcopy(original)
            lc0=next(value for value in missing['instances'].values() if value['family']=='lc0')
            lc0['options'].pop('Backend', None)
            path.write_text(json.dumps(missing))
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

            for backend in ('cuda', 'cudnn', 'opencl', 'onnx-cuda'):
                bad=copy.deepcopy(original)
                lc0=next(value for value in bad['instances'].values() if value['family']=='lc0')
                lc0['options']['Backend']=backend
                path.write_text(json.dumps(bad))
                with self.subTest(backend=backend), self.assertRaises(RuntimeError):
                    load_runtime_config(path)

            safe=copy.deepcopy(original)
            lc0=next(value for value in safe['instances'].values() if value['family']=='lc0')
            lc0['options']['Backend']='blas'
            path.write_text(json.dumps(safe))
            self.assertIsNotNone(load_runtime_config(path).online_time)


if __name__=='__main__':unittest.main()
