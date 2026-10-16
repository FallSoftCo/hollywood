"""Driver preparation/gates only. Synthetic records below are verifier unit data.

No PostgreSQL, HTTP server, controller, systemd unit or model is started.
"""
import copy
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('native_driver', Path(__file__).with_name('completed_report_native_driver.py'))
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-driver-unit-', dir=driver.ARTIFACTS)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fresh_generation_starts_nothing_and_keeps_fixture_credentials_private(self):
        with patch.object(driver.subprocess, 'run', side_effect=AssertionError('No subprocess permitted')):
            first, plan = driver.prepare(self.root)
            second, other = driver.prepare(self.root)
        self.assertNotEqual(first, second)
        self.assertTrue(set(plan['ids'].values()).isdisjoint(other['ids'].values()))
        self.assertEqual(plan['state'], 'prepared_no_services_no_native')
        private = first / 'fixture-credentials.private.json'
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        public = (first / 'plan.json').read_text()
        for item in json.loads(private.read_text()).values():
            self.assertNotIn(item['token'], public)
        self.assertFalse(Path(plan['database']['host']).exists())

    def test_commands_use_finite_native_guardians_and_real_controller(self):
        root, plan = driver.prepare(self.root)
        self.assertIn('--serve', plan['commands']['controller'])
        for actor in ('worker', 'reviewer'):
            command = plan['commands'][actor]
            self.assertIn('--claim-id', command)
            self.assertNotIn('--serve', command)
            unit = (root / 'units' / plan['units'][actor]).read_text()
            self.assertIn('Restart=no', unit)
            self.assertIn('KillMode=control-group', unit)
            self.assertNotIn('HOME=', unit)
        self.assertEqual(len(set(plan['claims'].values())), 3)

    def test_missing_admission_holds_before_any_subprocess_or_import(self):
        root, plan = driver.prepare(self.root)
        with patch.object(driver.subprocess, 'run', side_effect=AssertionError('Must not execute')):
            with self.assertRaisesRegex(driver.Held, 'admission is missing'):
                driver.check_capability(plan, None, None)
        self.assertFalse((root / 'proof-admission-used.json').exists())

    def test_missing_namespace_identifies_real_observer_and_provider_requirement(self):
        root, plan = driver.prepare(self.root)
        with self.assertRaisesRegex(driver.Held, 'actual NSS'):
            driver.check_capability(plan, root / 'not-read', None)

    def test_runtime_gate_rejects_unprotected_evidence(self):
        path = self.root / 'fake-capability.json'
        path.write_text('{}')
        path.chmod(0o666)
        with self.assertRaisesRegex(driver.Held, 'Root-protected'):
            driver.protected(path)

    def test_actual_server_policy_model_accepts_generated_wire_without_services(self):
        from hollywood_backbone.server import HostAdmissionPolicy
        HostAdmissionPolicy(**driver.host_policy(), operationId='unit-only', revision=0,
                            controllerId='00000000-0000-0000-0000-000000000001', machineIdentity='a' * 64)

    def test_readiness_uses_kernel_event_for_actual_owned_file(self):
        def create():
            (self.root / 'ready.pending').write_text('actual new local file')
            os.rename(self.root / 'ready.pending', self.root / 'ready')
        thread = threading.Thread(target=create)
        thread.start()
        driver.wait_socket(self.root, 'ready', 2)
        thread.join()
        self.assertEqual((self.root / 'ready').read_text(), 'actual new local file')

    def test_readiness_deadline_holds(self):
        with self.assertRaisesRegex(driver.Held, 'deadline exceeded'):
            driver.wait_socket(self.root, 'absent', 0.001)

    def test_postgres_readiness_requires_exact_pid_and_ready_status_without_database(self):
        # Local text-only unit input, not a database or executor receipt.
        (self.root / 'postmaster.pid').write_text('123\n\n\n\n\n\n\nstarting\n')
        with self.assertRaises(driver.Held):
            driver.wait_postgres(self.root, 123, 0.001)
        (self.root / 'postmaster.pid').write_text('123\n\n\n\n\n\n\nready\n')
        driver.wait_postgres(self.root, 123, 0.1)
        with self.assertRaises(driver.Held):
            driver.wait_postgres(self.root, 456, 0.001)


def synthetic_snapshot():
    # Pure verifier unit input; never inserted into a Store or called native proof.
    return {'original': {'id': 'original'}, 'workerContext': [{'context': 'same'}],
            'originalBinding': [{'binding': 'same'}], 'originalEffects': [{'effect': 'same'}],
            'originalActivity': [{'action': 'same'}], 'originalRecords': [{'event': 'same'}],
            'attempts': [{'number': 1, 'briefing_sequence': 42, 'report_event': None}],
            'work': {key: key for key in ('id', 'owner', 'goal', 'room', 'specification',
                     'acceptance', 'request_event', 'delivery')},
            'counter': '1\n', 'artifacts': [{'sha256': 'a' * 64}]}


class EvidenceVerifierTests(unittest.TestCase):
    def test_additive_report_and_review_do_not_rewrite_original_history(self):
        before = synthetic_snapshot()
        after = copy.deepcopy(before)
        after['attempts'][0].update(report_event='later-report', review_decision='accepted')
        after['work']['state'] = 'accepted'
        driver.assert_preserved(before, after)
        for path in ('original', 'originalBinding', 'originalEffects', 'originalRecords', 'artifacts'):
            changed = copy.deepcopy(after)
            changed[path] = ['changed']
            with self.assertRaises(driver.Held):
                driver.assert_preserved(before, changed)

    def test_counter_extra_attempt_or_briefing_rewrite_cannot_pass(self):
        before = synthetic_snapshot()
        for change in ('counter', 'attempt', 'briefing'):
            after = copy.deepcopy(before)
            if change == 'counter':
                after['counter'] = '2\n'
            elif change == 'attempt':
                after['attempts'].append({'number': 2})
            else:
                after['attempts'][0]['briefing_sequence'] = 99
            with self.assertRaises(driver.Held):
                driver.assert_preserved(before, after)

    def native_records(self):
        base = {'id': 'executor', 'delivery': 'd', 'agent': 'a', 'host': 'h', 'generation': 1,
                'claim_id': 'c', 'controller': 'controller', 'observed_at': 'observed',
                'launch_phase': 'attached', 'descriptor': {'nativePid': 123},
                'state': 'live', 'evidence': {'reason': 'matched-live-incarnation'}}
        reservation = {**base, 'launch_phase': 'reserved', 'descriptor': {'nativePid': None},
                       'state': 'captured', 'controller': None, 'observed_at': None, 'evidence': None}
        attached = {**base, 'state': 'captured', 'controller': None, 'observed_at': None, 'evidence': None}
        stopped = {**base, 'state': 'stopped', 'evidence': {'reason': 'registered-unit-drained'}}
        return {'deliveries': [{'id': 'd', 'agent': 'a', 'state': 'completed', 'result_event': 'r',
                               'native_thread': 't', 'native_turn': 'turn', 'claim_id': 'c',
                               'host': 'h', 'generation': 1}],
                'executorCommands': [{'result': {'executor': value}} for value in (reservation, attached, base, stopped)]}

    def test_completion_without_reserve_attach_independent_live_and_stop_is_insufficient(self):
        value = self.native_records()
        driver.assert_native_phase(value, 'd', 'a')
        for index in range(4):
            incomplete = copy.deepcopy(value)
            incomplete['executorCommands'].pop(index)
            with self.assertRaises(driver.Held):
                driver.assert_native_phase(incomplete, 'd', 'a')
        value['executorCommands'][2]['result']['executor']['controller'] = None
        with self.assertRaises(driver.Held):
            driver.assert_native_phase(value, 'd', 'a')

    def test_plain_completed_http_fixture_cannot_substitute_for_actual_native_tool(self):
        snapshot = {'deliveries': [{'id': 'd'}], 'allEffects': [], 'events': []}
        with self.assertRaisesRegex(driver.Held, 'actual receipted native tool'):
            driver.assert_tool(snapshot, 'd', 'a', 'hollywood_work_report_reconcile')

    def test_actual_tool_source_and_both_action_phases_are_required(self):
        tool = 'hollywood_work_report_reconcile'
        row = {'id': 'd', 'agent': 'a', 'host': 'h', 'event': 'request', 'claim_id': 'c',
               'generation': 1, 'native_thread': 'thread', 'native_turn': 'turn'}
        source = {'schema': 'hollywood-native-source/v2', 'deliveryId': 'd', 'agentId': 'a',
                  'hostId': 'h', 'requestId': 'request', 'claimId': 'c', 'generation': 1,
                  'nativeThread': 'thread', 'nativeTurn': 'turn', 'callId': 'call', 'tool': tool}
        activity = {'type': 'executionActivity', 'deliveryId': 'd', 'nativeType': 'dynamicToolCall',
                    'title': tool, 'actionId': 'action', 'itemId': 'call', 'thread': 'thread', 'turn': 'turn'}
        snapshot = {'deliveries': [row], 'work': {'id': 'work'}, 'allEffects': [{
            'delivery': 'd', 'principal': 'a', 'source': source, 'method': 'POST',
            'path': '/hollywood/v3/work/work/report-reconcile', 'wire_body_captured': True}],
            'events': [{'data': {**activity, 'phase': phase}} for phase in ('started', 'completed')]}
        self.assertEqual(driver.assert_tool(snapshot, 'd', 'a', tool), source)
        snapshot['events'].pop()
        with self.assertRaises(driver.Held):
            driver.assert_tool(snapshot, 'd', 'a', tool)


if __name__ == '__main__':
    unittest.main()
