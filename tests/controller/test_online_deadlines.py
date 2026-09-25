#!/usr/bin/env python3
"""Deadline behavior on actual fake-engine subprocesses, not mocked policy calls."""
import io
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from controller.uci_frontend import ShellState
from controller.replay import verify_bundle_integrity
from tests.controller.online_helpers import shell_fixture,wait_for

ANCHOR='stockfish-anchor'
SHADOWS=('stockfish-shadow','reckless-shadow','lc0-shadow')


def bestmoves(output):
    return [x for x in output.getvalue().splitlines() if x.startswith('bestmove ')]


class DeadlineTests(unittest.TestCase):
    def test_normal_outward_move_and_bound_replay(self):
        with shell_fixture() as (shell,manager,shadow,out,tmp):
            shell.handle_command('go movetime 500')
            wait_for(lambda:len(bestmoves(out))==1)
            self.assertEqual(bestmoves(out),['bestmove e2e4'])
            wait_for(lambda: list(tmp.glob('replays/*/route.json')))
            run=next(tmp.glob('replays/*'))
            manifest=json.loads((run/'manifest.json').read_text())
            self.assertEqual(verify_bundle_integrity(run),[])
            self.assertEqual(manifest['external_request']['command'],'go movetime 500')
            self.assertEqual(manifest['time_plan']['anchor_go_command'],'go movetime 390')
            self.assertTrue(manifest['clock_outcome']['output_within_deadline'])
            route=json.loads((run/'route.json').read_text())
            self.assertEqual(route['time_plan']['plan_id'],manifest['time_plan']['plan_id'])
            self.assertTrue(route['envelope_claim']['clock_output_complete'])
            self.assertTrue(route['envelope_claim']['claimed'])

    def test_soft_stop_actual_engine_ignoring_movetime(self):
        with shell_fixture(args={ANCHOR:['--info-lines','200','--info-delay-ms','10']},observe=False) as (shell,manager,shadow,out,tmp):
            t=time.monotonic();shell.handle_command('go movetime 300')
            wait_for(lambda:len(bestmoves(out))==1,timeout=1)
            elapsed=(time.monotonic()-t)*1000
            self.assertEqual(bestmoves(out),['bestmove e2e4'])
            self.assertGreater(elapsed,150)
            self.assertLess(elapsed,400)
            self.assertTrue(shell._clock_search.outcome()['output_within_deadline'])
            self.assertEqual(shell.state,ShellState.READY)

    def test_ignoring_stop_is_one_explicit_failure_not_fabricated_move(self):
        with shell_fixture(args={ANCHOR:['--info-lines','200','--info-delay-ms','10','--ignore-stop']},observe=False) as (shell,manager,shadow,out,tmp):
            shell.handle_command('go movetime 250')
            wait_for(lambda:len(bestmoves(out))==1,1)
            self.assertEqual(bestmoves(out),['bestmove 0000'])
            self.assertEqual(shell.state,ShellState.UNHEALTHY)
            self.assertFalse(shell._clock_search.outcome()['output_within_deadline'])
            wait_for(lambda:not manager.anchor.alive,1)
            shell._on_search_complete(1,'bestmove d2d4')
            self.assertEqual(bestmoves(out),['bestmove 0000'])

    def test_blocked_stop_write_does_not_block_hard_watchdog(self):
        with shell_fixture(args={ANCHOR:['--info-lines','200','--info-delay-ms','10','--ignore-stop']},observe=False) as (shell,manager,shadow,out,tmp):
            released=threading.Event();entered=threading.Event()
            def blocked(*args,**kwargs):
                entered.set();released.wait(2)
            with patch.object(manager,'stop_anchor_for',side_effect=blocked):
                try:
                    shell.handle_command('go movetime 250')
                    self.assertTrue(entered.wait(1))
                    wait_for(lambda:len(bestmoves(out))==1,1)
                    self.assertEqual(bestmoves(out),['bestmove 0000'])
                finally:released.set()

    def test_blocked_observer_cannot_delay_outward_bestmove(self):
        with shell_fixture() as (shell,manager,shadow,out,tmp):
            entered=threading.Event();release=threading.Event();original=manager._observer
            def observe(instance,*args):
                if instance==ANCHOR:
                    entered.set();release.wait(2)
                original(instance,*args)
            manager.set_instance_observer(observe)
            try:
                shell.handle_command('go movetime 500')
                self.assertTrue(entered.wait(1))
                wait_for(lambda:len(bestmoves(out))==1,1)
                self.assertEqual(bestmoves(out),['bestmove e2e4'])
                self.assertFalse(release.is_set())
                self.assertTrue(shell._clock_search.outcome()['output_within_deadline'])
            finally:release.set()
            wait_for(lambda:list(tmp.glob('replays/*/route.json')))

    def test_blocked_replay_setup_uses_same_preparation_deadline(self):
        with shell_fixture() as (shell,manager,shadow,out,tmp):
            original_mkdir=Path.mkdir
            def mkdir(path,*args,**kwargs):
                if 'replays' in path.parts:
                    time.sleep(.15)
                return original_mkdir(path,*args,**kwargs)
            with patch.object(Path,'mkdir',mkdir):
                t=time.monotonic();shell.handle_command('go movetime 500')
                wait_for(lambda:len(bestmoves(out))==1,1)
                self.assertLess(time.monotonic()-t,.4)
                self.assertEqual(bestmoves(out),['bestmove e2e4'])
            time.sleep(.2) # allow abandoned filesystem worker to discard its result

    def test_user_stop_and_late_generation_stop_cannot_affect_next_search(self):
        args={ANCHOR:['--info-lines','200','--info-delay-ms','10']}
        with shell_fixture(args=args,observe=False) as (shell,manager,shadow,out,tmp):
            shell.handle_command('go movetime 500')
            shell.handle_command('stop')
            wait_for(lambda:len(bestmoves(out))==1)
            shell.handle_command('position startpos moves e2e4')
            shell.handle_command('go movetime 500')
            self.assertFalse(manager.stop_anchor_for(1,timeout=.05))
            shell._on_search_complete(1,'bestmove d2d4')
            time.sleep(.05)
            self.assertEqual(len(bestmoves(out)),1)
            shell.handle_command('stop')
            wait_for(lambda:len(bestmoves(out))==2)
            self.assertEqual(shell._clock_search.plan.side_to_move,'b')

    def test_rejected_requests_do_not_dispatch(self):
        with shell_fixture(observe=False) as (shell,manager,shadow,out,tmp):
            with patch.object(manager,'start_anchor_search',wraps=manager.start_anchor_search) as start:
                shell.handle_command('go wtime 0 btime 60000')
                shell.handle_command('go movetime 300 depth 9')
                shell.handle_command('go infinite')
                start.assert_not_called()
            self.assertEqual(bestmoves(out),['bestmove 0000']*3)
            self.assertEqual(shell.state,ShellState.READY)

    def test_stuck_shadow_is_quarantined_without_flagging_anchor(self):
        args={name:['--info-lines','200','--info-delay-ms','10','--ignore-stop'] for name in SHADOWS}
        with shell_fixture(args=args) as (shell,manager,shadow,out,tmp):
            shell.handle_command('go movetime 400')
            wait_for(lambda:len(bestmoves(out))==1)
            self.assertEqual(bestmoves(out),['bestmove e2e4'])
            wait_for(lambda:all(not manager.process(n).alive for n in SHADOWS),1.5)
            self.assertTrue(manager.healthy)
            shell.handle_command('position startpos moves e2e4')
            shell.handle_command('go movetime 400')
            wait_for(lambda:len(bestmoves(out))==2)
            self.assertEqual(bestmoves(out)[-1],'bestmove e2e4')

    def test_repeated_successful_go_and_isready_do_not_emit_duplicates(self):
        with shell_fixture(observe=False) as (shell,manager,shadow,out,tmp):
            for i in range(4):
                shell.handle_command('position startpos'+(' moves e2e4' if i%2 else ''))
                shell.handle_command('go wtime 60000 btime 30000 winc 500 binc 100')
                shell.handle_command('isready')
                wait_for(lambda:len(bestmoves(out))==i+1)
                self.assertEqual(shell._clock_search.plan.side_to_move,'b' if i%2 else 'w')
            time.sleep(.2)
            self.assertEqual(bestmoves(out),['bestmove e2e4']*4)
            self.assertEqual(out.getvalue().splitlines().count('readyok'),4)

    def test_expired_receipt_is_not_rebased_on_dispatch(self):
        with shell_fixture(observe=False) as (shell,manager,shadow,out,tmp):
            shell._receipt_monotonic=time.monotonic()-2
            shell._receipt_cpu_ns=time.process_time_ns()
            with patch.object(manager,'start_anchor_search',wraps=manager.start_anchor_search) as start:
                shell.handle_command('go movetime 500')
                wait_for(lambda:len(bestmoves(out))==1)
                start.assert_not_called()
            self.assertFalse(shell._clock_search.outcome()['output_within_deadline'])

    def test_clock_mode_never_calls_hybrid_authorization(self):
        with shell_fixture(observe=False) as (shell,manager,shadow,out,tmp):
            with patch('controller.decision.authorize_decision',side_effect=AssertionError('authority expanded')) as authorize:
                shell.handle_command('go movetime 500')
                wait_for(lambda:len(bestmoves(out))==1)
                authorize.assert_not_called()
            shell.handle_command('setoption name UCI_Chess960 value true')
            self.assertFalse(manager.chess960)

    def test_deferred_queue_loss_is_tainted_and_terminal_has_reserved_slot(self):
        from adapters.process.deferred_observer import DeferredObserver
        entered=threading.Event();released=threading.Event();observed=[];finished=[]
        def consume(token,line,stamp):
            entered.set();released.wait(2);observed.append(line)
        worker=DeferredObserver(observe=consume,finished=lambda *args:finished.append(args),capacity=1)
        try:
            worker.submit(1,'info depth 1',1.)
            self.assertTrue(entered.wait(1))
            worker.submit(1,'info depth 2',2.)
            worker.submit(1,'info depth 3',3.)
            worker.submit(1,'bestmove e2e4',4.)
            released.set()
            self.assertTrue(worker.done.wait(1))
            self.assertEqual(observed,['info depth 1','info depth 2','bestmove e2e4'])
            self.assertEqual(finished,[(1,'bestmove e2e4',1)])
        finally:released.set();worker.abort()

    def test_delayed_old_measurement_cannot_qualify_across_new_anchor(self):
        with shell_fixture() as (shell,manager,shadow,out,tmp):
            release=threading.Event();entered=threading.Event();original=manager._observer
            def observe(instance,token,*args):
                if instance==ANCHOR and token==1:
                    entered.set();release.wait(3)
                original(instance,token,*args)
            manager.set_instance_observer(observe)
            try:
                shell.handle_command('go movetime 500')
                self.assertTrue(entered.wait(1))
                wait_for(lambda:len(bestmoves(out))==1)
                old=shell._clock_search
                shell.handle_command('go movetime 500')
                self.assertTrue(old.measurement_superseded.is_set())
                release.set()
                wait_for(lambda:len(bestmoves(out))==2)
                wait_for(lambda:list(tmp.glob('replays/*/route.json')))
                first=next(tmp.glob('replays/*'))
                route=json.loads((first/'route.json').read_text())
                resource=json.loads((first/'resource.json').read_text())
                self.assertFalse(route['envelope_claim']['claimed'])
                self.assertFalse(resource['qualified'])
                self.assertIn('generation',resource['interval_error'])
            finally:release.set()

    def test_dispatch_permit_is_rechecked_after_slow_stage_preparation(self):
        args={ANCHOR:['--info-lines','200','--info-delay-ms','10']}
        with shell_fixture(args=args) as (shell,manager,shadow,out,tmp):
            entered=threading.Event();original=shadow._begin_resource_stage
            def slow(active,**kwargs):
                if kwargs.get('phase')=='EXPLORE':
                    entered.set();time.sleep(.3)
                return original(active,**kwargs)
            with patch.object(shadow,'_begin_resource_stage',side_effect=slow):
                shell.handle_command('go movetime 300')
                self.assertTrue(entered.wait(1))
                wait_for(lambda:len(bestmoves(out))==1,1)
                self.assertEqual(bestmoves(out),['bestmove e2e4'])
                wait_for(lambda:list(tmp.glob('replays/*/route.json')))
                for name in SHADOWS:
                    transcript=manager.process(name)._transcript
                    self.assertFalse(any(line.startswith('>> go nodes') for line in transcript))


if __name__=='__main__':unittest.main()
