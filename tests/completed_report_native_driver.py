"""Prepare, gate and run ONE fresh native proof. Default operation starts nothing.

run requires a separately reviewed, root-protected isolated managed namespace.
This file never provisions Linux accounts, copies auth, or manufactures executor
records. The current host has no approved namespace capability; see the doc.
"""
import argparse
import asyncio
import hashlib
import json
import os
import pwd
import secrets
import signal
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

HOLLYWOOD = '831adf9993044d325710b80e07d891bbd7ea6a67'
LAX = '31759cede2bff54c46a2047efeaf7aab5b1990ae'
OPERATION = 'completed-work-report-recovery-20261016-r01'
RESERVE, HEADROOM = 80 * 1024**3, 48 * 1024**2
PYTHON = '/opt/harness/production-core-20261016-r03/venv/bin/python'
NATIVE = '/opt/harness/production-core-20261016-r03/native/bin/codex'
SOURCE = Path(__file__).resolve().parents[1]
SCRATCH = SOURCE.parent
ARTIFACTS = SCRATCH / 'artifacts-hollywood/driver-preparation-r04'
PRODUCTION_AUTHORITY = 'e488f19b-0f60-46ca-bd48-4fe544f06757'
MISSING = (
    'A reviewed RAM-backed private mount/network/systemd namespace with genuine '
    'root-protected host enrollment and worker/reviewer provisioning, actual NSS '
    'accounts matching service_observer.expected_owner, finite registered lax-*.service '
    'guardians, an approved in-place provider route into fresh native homes without '
    'credential copying, and reviewer read access to original artifact bytes. '
    'Unchanged linux_worker.provision copies auth.json and enables a resident service; '
    'it does not satisfy this preparation contract.'
)


class Held(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Held(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write(path, value):
    # Each output is new or belongs to this fresh run; preserve failed runs.
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, default=str)
        output.write('\n')


def reserve():
    fs = os.statvfs('/aosp')
    free = fs.f_bavail * fs.f_frsize
    require(free >= RESERVE + HEADROOM, f'/aosp phase reserve unmet: {free}')
    return free


def ram(path):
    path = Path(path).resolve()
    require(path.is_relative_to(Path('/dev/shm')), 'Fresh RAM paths required')
    require(path.stat().st_dev == Path('/dev/shm').stat().st_dev, 'RAM backing required')
    return path


def protected(path):
    path = Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            f'Root-protected regular evidence required: {path.name}')
    return json.loads(path.read_text())


def namespace_id(name):
    return os.readlink('/proc/self/ns/' + name)


def socket_identity(path):
    info = Path(path).stat()
    return [info.st_dev, info.st_ino]


def host_policy():
    return {'maxConcurrent': 1, 'reservedInteractive': 0, 'controlAgentIds': [],
            'memoryPerTurnBytes': 1, 'reserveMemoryBytes': 0,
            'sampleMaxAgeSeconds': 30, 'connectorMaxAgeSeconds': 30}


def worker_home(authority, agent):
    return Path('/var/lib/harness') / authority / 'workers' / agent / 'home'


def unit_name(authority, agent):
    return 'lax-worker-' + authority.replace('-', '')[:8] + '-' + agent.replace('-', '')[:12] + '.service'


def command_templates(root, ids):
    root = Path(root)
    return {
        'hostEnroll': [PYTHON, '-m', 'lax_connector.host', '--enroll', str(root / 'host-spec.json')],
        'controller': [PYTHON, '-m', 'lax_connector.host', '--serve',
                       f"/var/lib/harness/{ids['authority']}/hosts/{ids['host']}/controller.json"],
        'worker': [PYTHON, '-m', 'lax_connector.connector', '--config',
                   str(worker_home(ids['authority'], ids['worker']) / 'connector.json'),
                   '--claim-id', '${LAX_CLAIM_ID}'],
        'reviewer': [PYTHON, '-m', 'lax_connector.connector', '--config',
                     str(worker_home(ids['authority'], ids['reviewer']) / 'connector.json'),
                     '--claim-id', '${LAX_CLAIM_ID}'],
        'unitStart': ['systemctl', '--wait', 'start', '<exact-owned-unit>'],
        'unitStop': ['systemctl', 'stop', '<same-owned-invocation-only>'],
    }


def original_prompt():
    return (
        'SYNTHETIC isolated native qualification only. Call hollywood_context and '
        'hollywood_briefing. In the captured original workspace, use one actual shell '
        'action to create effect-count.txt with exclusive creation and contents 1\\n, '
        'and effect.json with keys deliveryId, requestId, contextId, nativeThread, '
        'hostId and counterSha256 containing those actual identities/hash. '
        'Abort if either file already exists. '
        'Read the bytes and record SHA256s. End normally with these facts. '
        'Leave this work assigned and UNREPORTED: do not call any work report or '
        'review tool. Do not create requests, agents, goals, timers or other effects.'
    )


def later_prompt(work, delivery):
    return (
        f'SYNTHETIC isolated qualification. This is later ordinary participation, '
        f'not another work assignment. Call hollywood_context and briefing, then read '
        f'work {work}, its original execution {delivery}, native effect receipts, '
        'and the actual effect-count.txt/effect.json bytes in the SAME workspace. '
        'If the counter is exactly 1, the original completed unreported, and all '
        'effects/controls are accounted for, call the actual '
        f'hollywood_work_report_reconcile with workId={work}, the CURRENT revision, '
        f'priorDeliveryId={delivery}, state=submitted, effectDisposition=reviewed, '
        'artifactPaths=[effect-count.txt,effect.json], and an explicit truthful note '
        'describing inspected receipts. If anything is unknown, report that blocker '
        'and do not claim success. Do not rewrite files, rerun the original command, '
        'assign/recover work, or invoke ordinary work_report. End after the receipt.'
    )


def review_prompt(work, original_workspace):
    return (
        f'SYNTHETIC isolated qualification. Independently inspect work {work}, '
        'the original completed delivery/attempt, later reconciliation and actual '
        f'original artifact bytes through the approved read-only route {original_workspace}. '
        'Call hollywood_context and briefing. Verify SHA256s, counter exactly 1, '
        'one original attempt, separate later native source and no original replay. '
        'Use the ordinary hollywood_work_review with the current revision to accept '
        'only if those facts are verified; otherwise reject or describe a blocker. '
        'You are distinct from the worker. Do not assign/recover work, change files, '
        'or request further model executions. End after the ordinary review receipt.'
    )


def prepare(base=ARTIFACTS):
    os.umask(0o077)
    free = reserve()
    Path(base).mkdir(parents=True, exist_ok=True)
    base = ram(base)
    root = Path(tempfile.mkdtemp(prefix='native-prepared-', dir=base))
    for name in ('tmp', 'logs', 'proof', 'units'):
        (root / name).mkdir(mode=0o700)
    ids = {name: str(uuid4()) for name in ('authority', 'host', 'room', 'owner',
                                        'worker', 'reviewer', 'goal', 'work')}
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    sock = SOURCE / ('.n-' + uuid4().hex[:8])
    require(len(str(sock)) + 25 < 108, 'PostgreSQL socket path too long')
    plan = {
        'schema': 'completed-report-native-driver/v1', 'operationId': OPERATION,
        'state': 'prepared_no_services_no_native', 'root': str(root), 'ids': ids,
        'cuts': {'hollywoodDomain': HOLLYWOOD, 'lax': LAX},
        'source': {'hollywood': str(SOURCE), 'lax': str(SCRATCH / 'lax/losangelex/connector')},
        'python': PYTHON, 'native': NATIVE, 'url': url, 'port': port,
        'database': {'host': str(sock), 'port': port, 'dbname': 'report_recovery_native'},
        'outsideNamespaces': {name: namespace_id(name) for name in ('mnt', 'net', 'pid')},
        'outsideSystemBus': socket_identity('/run/systemd/private'),
        'observedAospAvailableBytes': free, 'commands': command_templates(root, ids),
        'units': {name: unit_name(ids['authority'], ids[name]) for name in ('worker', 'reviewer')},
        'claims': {name: 'native-proof:' + str(uuid4()) for name in ('original', 'later', 'review')},
        'limits': {'perPhaseSeconds': 240, 'cleanupSeconds': 30, 'nativeAdmissions': 3},
        'missingCapability': MISSING, 'synthetic': 'Only local authority/credentials/goal metadata; never native lifecycle or human permission.',
        'runtimeAdmission': 'Separate Global admission pinned to this plan SHA256 and reviewed driver source SHA256; not granted by preparation.',
    }
    # New fixture-only credentials. No existing account credential is read/copied.
    credentials = {name: {'principalId': ids[key], 'token': secrets.token_urlsafe(40)}
                   for name, key in (('Owner', 'owner'), ('Global', 'reviewer'), ('Worker', 'worker'))}
    write(root / 'fixture-credentials.private.json', credentials)
    write(root / 'prompts.json', {'original': original_prompt(),
                                'laterTemplate': later_prompt(ids['work'], '<original-delivery-from-normal-assignment>'),
                                'review': review_prompt(ids['work'], str(worker_home(ids['authority'], ids['worker']) / 'work'))})
    for name in ('worker', 'reviewer'):
        agent, home = ids[name], worker_home(ids['authority'], ids[name])
        user = 'hworker-' + ids['authority'].replace('-', '')[:8] + '-' + agent.replace('-', '')[:12]
        config = {'hollywoodUrl': url, 'agentId': agent, 'hostId': ids['host'],
                  'tokenReference': f'fixture-credentials.private.json:{"Worker" if name == "worker" else "Global"}',
                  'state': str(home / 'state'), 'workspace': str(home / 'work'),
                  'codexHome': str(home / 'codex-home'), 'binary': NATIVE,
                  'providerRequirement': 'Approved existing route in place; fresh native history; no auth/config copy.',
                  'modelRequirement': 'Use the separately approved existing provider/model/effort; no implicit model selection.',
                  'sandbox': 'danger-full-access',
                  'instructions': 'Execute only the addressed isolated proof phase. Call hollywood_context first. Preserve unknown effects and wait after completion.'}
        write(root / f'{name}-configuration-template.json', config)
        unit = ('[Unit]\nDescription=Fresh isolated native proof\n[Service]\nType=simple\n'
                f'User={user}\nWorkingDirectory={home / "work"}\n'
                f'Environment=PYTHONDONTWRITEBYTECODE=1\nEnvironment=TMPDIR={root / "tmp"}\n'
                f'Environment=PYTHONPATH={plan["source"]["lax"]}\n'
                f'EnvironmentFile={root / (name + "-claim.env")}\n'
                f'ExecStart={PYTHON} -m lax_connector.connector --config {home / "connector.json"} --claim-id ${{LAX_CLAIM_ID}}\n'
                f'StandardOutput=append:{root / "logs" / (name + "-connector.log")}\nStandardError=inherit\n'
                'Restart=no\nKillMode=control-group\nUMask=0077\nTimeoutStopSec=20\nMemoryMax=2G\n')
        (root / 'units' / plan['units'][name]).write_text(unit)
    write(root / 'capability-required.json', {'schema': 'native-proof-namespace-required/v1',
          'missing': MISSING, 'requirements': ['New private mnt/net/pid namespace and private system bus',
          'RAM /var/lib/harness, /etc NSS/unit changes and /run; production registry invisible',
          'Root-protected real provisioning and registration for exactly these fixture IDs',
          'Actual nonroot pgUser and actual worker/reviewer NSS homes',
          'No model started by namespace setup; finite units inactive',
          'Provider route approved in place; fresh native homes; original artifacts readable by reviewer'],
          'executionAvailableNow': False})
    plan['files'] = {str(path.relative_to(root)): sha(path.read_bytes())
                     for path in sorted(root.rglob('*')) if path.is_file()}
    write(root / 'plan.json', plan)
    return root, plan


def check_capability(plan, admission_path, capability_path):
    require(admission_path is not None, 'Separate Global proof admission is missing')
    require(capability_path is not None, MISSING)
    admission, cap = protected(admission_path), protected(capability_path)
    root, ids = ram(plan['root']), plan['ids']
    require(root.is_relative_to(ARTIFACTS) and plan['cuts'] == {'hollywoodDomain': HOLLYWOOD, 'lax': LAX}
            and plan['source'] == {'hollywood': str(SOURCE), 'lax': str(SCRATCH / 'lax/losangelex/connector')}
            and plan['commands'] == command_templates(root, ids)
            and plan['url'] == f"http://127.0.0.1:{plan['port']}"
            and plan['limits'] == {'perPhaseSeconds': 240, 'cleanupSeconds': 30, 'nativeAdmissions': 3},
            'Prepared pair, paths, commands or limits differ')
    for relative, expected in plan['files'].items():
        path = root / relative
        require(path.resolve().is_relative_to(root) and sha(path.read_bytes()) == expected,
                'Prepared input bytes differ from exact admitted plan')
    require(admission.get('schema') == 'completed-report-native-proof-admission/v1'
            and admission.get('operationId') == OPERATION
            and admission.get('planSha256') == sha((root / 'plan.json').read_bytes())
            and admission.get('driverSha256') == sha(Path(__file__).read_bytes())
            and admission.get('maxNativeAdmissions') == 3
            and admission.get('sourceEventId') and admission.get('approvedByGlobal') is True,
            'Separate exact one-proof Global admission required')
    require(os.geteuid() == 0, 'Managed proof runtime requires namespace administrator')
    require(ids['authority'] != PRODUCTION_AUTHORITY, 'Production authority forbidden')
    for name in ('mnt', 'net', 'pid'):
        require(namespace_id(name) != plan['outsideNamespaces'][name], f'Private {name} namespace missing')
    require(cap.get('schema') == 'completed-report-managed-namespace/v1'
            and cap.get('planSha256') == admission['planSha256']
            and cap.get('ids') == ids and cap.get('modelsStarted') == 0,
            'Exact zero-model namespace setup receipt missing')
    require(os.stat('/var/lib/harness').st_dev == os.stat('/dev/shm').st_dev,
            'Protected registry must be backed by the fresh RAM namespace')
    require(not (Path('/var/lib/harness') / PRODUCTION_AUTHORITY).exists(), 'Production registry visible')
    require(socket_identity('/run/systemd/private') != plan['outsideSystemBus'], 'Private system bus required')
    require(cap.get('providerApprovalSource') and cap.get('credentialsCopied') is False
            and cap.get('freshNativeHistory') is True, 'Reviewed in-place provider route unavailable')
    require(ram(cap['namespaceBackingRoot']).is_relative_to(root), 'Namespace backing state must remain in this RAM fixture')
    pg = pwd.getpwnam(cap['pgUser'])
    require(pg.pw_uid > 0, 'PostgreSQL must use a separate nonroot namespace account')
    for name in ('worker', 'reviewer'):
        home = worker_home(ids['authority'], ids[name])
        user = 'hworker-' + ids['authority'].replace('-', '')[:8] + '-' + ids[name].replace('-', '')[:12]
        account = pwd.getpwnam(user)
        require(account.pw_dir == str(home) and account.pw_uid > 0, 'Genuine worker NSS home mismatch')
        require(home.resolve().is_relative_to(Path('/var/lib/harness')), 'Worker home escaped registry')
        for child in ('work', 'state', 'codex-home'):
            path = home / child
            require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == account.pw_uid,
                    'Fresh native directory ownership mismatch')
        config = json.loads((home / 'connector.json').read_text())
        require((config['agentId'], config['hostId'], config['workspace'], config['state'],
                 config['codexHome'], config['binary'], config['hollywoodUrl']) ==
                (ids[name], ids['host'], str(home / 'work'), str(home / 'state'),
                 str(home / 'codex-home'), NATIVE, plan['url']), 'Connector identity/source path mismatch')
        require(config.get('model') and config.get('effort'), 'Approved model configuration required')
        fixture = json.loads((root / 'fixture-credentials.private.json').read_text())
        require(config.get('token') == fixture['Worker' if name == 'worker' else 'Global']['token'],
                'New fixture connector credential differs')
        require(not any((home / 'work').iterdir()) and not any((home / 'state').iterdir()),
                'Retained worker state/workspace forbidden')
        unit = Path('/etc/systemd/system') / plan['units'][name]
        info = unit.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and unit.read_bytes() == (root / 'units' / plan['units'][name]).read_bytes(),
                'Protected exact finite guardian definition missing')
    # A capability receipt cannot replace these actual source/namespace checks.
    require(cap.get('reviewerArtifactAccess') == str(worker_home(ids['authority'], ids['worker']) / 'work'),
            'Actual reviewer artifact read route required')
    for key, cut, path in (('hollywood', HOLLYWOOD, SOURCE),
                           ('lax', LAX, Path(plan['source']['lax']).parents[1])):
        args = ['git', '--no-optional-locks', '-c', f'safe.directory={path}', '-C', str(path)]
        require(subprocess.run(args + ['diff', '--quiet', cut, '--',
                'hollywood_backbone' if key == 'hollywood' else 'losangelex/connector'],
                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, check=False).returncode == 0,
                f'Exact qualified {key} source differs')
    import sys
    sys.path.insert(0, plan['source']['lax'])
    from lax_connector.service_observer import expected_owner, installed_unit, properties
    for name in ('worker', 'reviewer'):
        agent = ids[name]
        uid, home, binary = expected_owner(ids['authority'], agent)
        require(home == worker_home(ids['authority'], agent) and binary == NATIVE
                and uid == home.stat().st_uid, 'Protected installed native owner differs')
        require(installed_unit(ids['authority'], agent) == plan['units'][name],
                'Protected provision receipt does not name exact finite unit')
        info = properties(plan['units'][name])
        require(info['ActiveState'] == 'inactive' and info['MainPID'] == '0',
                'Namespace setup must leave all native units inactive')
    return cap


def immutable(snapshot):
    attempt = snapshot['attempts'][0]
    return {'delivery': snapshot['original'], 'context': snapshot['workerContext'],
            'binding': snapshot['originalBinding'], 'effects': snapshot['originalEffects'],
            'activity': snapshot['originalActivity'], 'records': snapshot['originalRecords'],
            'attempt': {key: value for key, value in attempt.items() if key not in
                        ('report_event', 'evidence', 'report_note', 'review_event',
                         'review_decision', 'review_note', 'report_reconciliation_event')},
            'assignment': {key: snapshot['work'][key] for key in
                           ('id', 'owner', 'goal', 'room', 'specification', 'acceptance',
                            'request_event', 'delivery')}}


def assert_preserved(before, after):
    require(immutable(before) == immutable(after), 'Original execution/assignment/history changed')
    require(len(after['attempts']) == 1, 'Extra original work attempt')
    require(after['counter'] == '1\n' and after['artifacts'] == before['artifacts'],
            'Original effect bytes/hash changed')


def assert_native_phase(snapshot, delivery, agent):
    row = next(x for x in snapshot['deliveries'] if x['id'] == delivery)
    require(row['agent'] == agent and row['state'] == 'completed' and row['result_event']
            and row['native_thread'] and row['native_turn'] and row['claim_id'],
            'Original normal native completion unavailable; hold, never re-admit')
    receipts = [x['result']['executor'] for x in snapshot['executorCommands']
                if x['result'].get('executor', {}).get('delivery') == delivery]
    require(receipts and len({x['id'] for x in receipts}) == 1
            and all((x['agent'], x['host'], x['generation'], x['claim_id']) ==
                    (agent, row['host'], row['generation'], row['claim_id']) for x in receipts),
            'Executor receipts changed original owner/host/claim/generation')
    require(any(x['launch_phase'] == 'reserved' and x['descriptor']['nativePid'] is None for x in receipts),
            'Genuine original pre-birth reservation receipt missing')
    require(any(x['launch_phase'] == 'attached' and x['descriptor']['nativePid']
                and x['state'] == 'captured' and x['controller'] is None and x['observed_at'] is None
                for x in receipts),
            'Genuine native attach receipt missing')
    require(any(x['state'] == 'live' and x['controller'] and x['observed_at']
                and x['evidence'].get('reason') == 'matched-live-incarnation' for x in receipts),
            'Independent live executor receipt missing')
    require(any(x['state'] == 'stopped' and x['controller'] and x['observed_at']
                and x['evidence'].get('reason') in ('registered-unit-drained', 'registered-invocation-settled')
                for x in receipts), 'Independent stopped executor receipt missing')


def assert_tool(snapshot, delivery, agent, tool):
    row = next(x for x in snapshot['deliveries'] if x['id'] == delivery)
    matches = [effect for effect in snapshot['allEffects'] if effect['delivery'] == delivery
               and effect['principal'] == agent and effect['source'].get('tool') == tool]
    require(len(matches) == 1, 'One actual receipted native tool mutation required')
    action = 'report-reconcile' if tool == 'hollywood_work_report_reconcile' else 'review'
    require(matches[0]['method'] == 'POST'
            and matches[0]['path'].endswith('/work/' + snapshot['work']['id'] + '/' + action)
            and matches[0]['wire_body_captured'] is True, 'Actual tool wire provenance incomplete')
    source = matches[0]['source']
    require(source.get('schema') == 'hollywood-native-source/v2'
            and source.get('deliveryId') == delivery and source.get('agentId') == agent
            and source.get('requestId') == row['event'] and source.get('claimId') == row['claim_id']
            and source.get('generation') == row['generation']
            and source.get('nativeThread') == row['native_thread']
            and source.get('nativeTurn') == row['native_turn']
            and source.get('hostId') == row['host'], 'Actual native tool source mismatch')
    activity = [event['data'] for event in snapshot['events']
                if event['data'].get('type') == 'executionActivity'
                and event['data'].get('deliveryId') == delivery
                and event['data'].get('nativeType') == 'dynamicToolCall'
                and event['data'].get('title') == tool]
    require({item['phase'] for item in activity} == {'started', 'completed'}
            and len({item['actionId'] for item in activity}) == 1
            and all(item['thread'] == source['nativeThread'] and item['turn'] == source['nativeTurn']
                    and item['itemId'] == source['callId'] for item in activity),
            'Actual native dynamic action lifecycle missing or mismatched')
    return source


async def run(plan, cap):
    """Future separately admitted execution only. No retry of native admissions."""
    import sys
    sys.path.insert(0, str(SOURCE))
    from aiohttp import ClientSession, ClientTimeout, web
    from hollywood_backbone.server import application
    from hollywood_backbone.store import Store, json_value
    import hollywood_backbone
    require(Path(hollywood_backbone.__file__).resolve().is_relative_to(SOURCE), 'Wrong server import')
    root, ids = Path(plan['root']), plan['ids']
    reserve()
    # Exclusive durable admission marker: interruption never silently restarts it.
    write(root / 'proof-admission-used.json', {'planSha256': sha((root / 'plan.json').read_bytes()),
          'state': 'admitted_once_outcome_pending', 'pid': os.getpid()})
    credentials = json.loads((root / 'fixture-credentials.private.json').read_text())
    pguser = pwd.getpwnam(cap['pgUser'])
    dependency = json.loads((SCRATCH / 'shared/pg-dependency-route-r01.json').read_text())
    # Dependency route is explicitly reviewed, not a package install or copy.
    dependency = dependency.get('postgres', dependency)
    bindir = Path(dependency['bindir'])
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': str(root / 'tmp'),
           'PYTHONPATH': plan['source']['lax'], 'LD_LIBRARY_PATH': dependency['libraryDir']}
    owned, cleanup, units = [], [], []
    runner, store, pg_pid, verified = None, None, None, None
    config = {**plan['database'], 'user': cap['pgUser']}

    def ticks(pid):
        return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19]

    async def launch(argv, label, as_pg=False):
        log = (root / 'logs' / (label + '.log')).open('xb')
        process = await asyncio.create_subprocess_exec(*argv, env=env, stdout=log, stderr=log,
                    user=pguser.pw_uid if as_pg else None,
                    group=pguser.pw_gid if as_pg else None, start_new_session=True)
        try:
            start = ticks(process.pid)
        except FileNotFoundError:
            start = None  # Already exited child remains owned via waitpid, never signal it.
        record = {'label': label, 'argv': argv, 'pid': process.pid, 'startTicks': start}
        owned.append((process, record, log))
        write(root / 'logs' / (label + '.process.json'), record)
        return process

    async def command(argv, label, as_pg=False, timeout=60):
        process = await launch(argv, label, as_pg)
        code = await asyncio.wait_for(process.wait(), timeout)
        require(code == 0, f'{label} failed ({code}); retain original effects/logs')

    def rows(query, args=()):
        with store.pool.connection() as connection:
            return json_value(connection.execute(query, args).fetchall())

    def snapshot(original):
        original_row = rows('SELECT * FROM hb_delivery WHERE id=%s', (original,))[0]
        result = {'original': original_row,
                  'deliveries': rows('SELECT * FROM hb_delivery ORDER BY id'),
                  'workerContext': rows('SELECT * FROM hb_agent_context WHERE principal=%s', (ids['worker'],)),
                  'originalBinding': rows('SELECT * FROM hb_agent_execution WHERE delivery=%s', (original,)),
                  'originalEffects': rows('SELECT * FROM hb_native_effect WHERE delivery=%s ORDER BY sequence', (original,)),
                  'allEffects': rows('SELECT * FROM hb_native_effect ORDER BY sequence'),
                  'originalActivity': rows('SELECT * FROM hb_activity WHERE delivery=%s ORDER BY item_id,phase', (original,)),
                  'originalRecords': rows('SELECT * FROM hb_event WHERE id IN '
                        '(SELECT event FROM hb_activity WHERE delivery=%s) OR id=%s OR id=%s ORDER BY seq',
                        (original, original_row['event'], original_row['result_event'])),
                  'attempts': rows('SELECT * FROM hb_work_attempt WHERE work=%s ORDER BY number', (ids['work'],)),
                  'work': rows('SELECT * FROM hb_work WHERE id=%s', (ids['work'],))[0],
                  'bindings': rows('SELECT * FROM hb_agent_execution ORDER BY delivery'),
                  'executorCommands': rows('SELECT * FROM hb_executor_command ORDER BY principal,operation'),
                  'workCommands': rows('SELECT * FROM hb_command ORDER BY principal,operation'),
                  'reconciliations': rows('SELECT * FROM hb_work_report_reconciliation'),
                  'events': rows('SELECT * FROM hb_event ORDER BY seq')}
        workspace = worker_home(ids['authority'], ids['worker']) / 'work'
        for name in ('effect-count.txt', 'effect.json'):
            metadata = (workspace / name).lstat()
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 65536,
                    'Original artifact must be a bounded regular file')
        result['counter'] = (workspace / 'effect-count.txt').read_text()
        result['artifacts'] = [{ 'relativePath': name, 'sha256': sha((workspace / name).read_bytes()),
                                 'size': (workspace / name).stat().st_size}
                               for name in ('effect-count.txt', 'effect.json')]
        return result

    try:
        # Namespace setup must have given the PG account access to this fresh root.
        sock = Path(config['host'])
        sock.mkdir(mode=0o700)
        os.chown(sock, pguser.pw_uid, pguser.pw_gid)
        data = root / 'pgdata'
        data.mkdir(mode=0o700)
        os.chown(data, pguser.pw_uid, pguser.pw_gid)
        await command([str(bindir / 'initdb'), '-D', str(data), '-U', cap['pgUser'],
                       '--auth=trust', '--no-locale', '-E', 'UTF8', '-L', dependency['shareDir']], 'initdb', True)
        # Run postgres directly: retain its PID/start token even on failed startup.
        pg = await launch([str(bindir / 'postgres'), '-D', str(data), '-h', '', '-k', config['host'],
                           '-p', str(config['port']), '-F', '-c', 'shared_buffers=16MB'], 'postgres', True)
        pg_pid = (pg.pid, ticks(pg.pid))
        # The socket can precede SQL readiness. Wait on actual postmaster status
        # writes, verifying the directly owned original PID, without a query loop.
        await asyncio.to_thread(wait_postgres, data, pg.pid, 30)
        await command([str(bindir / 'createdb'), '-h', config['host'], '-p', str(config['port']),
                       '-U', cap['pgUser'], config['dbname']], 'createdb', True)
        store = Store(config)
        store.migrate()
        store.bootstrap(ids['authority'], 'SYNTHETIC isolated native proof', {
            'globalRoomId': ids['room'], 'credentials': {k: credentials[k] for k in ('Owner', 'Global')}})
        owner = ids['owner']
        store.principal(owner, ids['worker'], 'SYNTHETIC native worker', 'agent', credentials['Worker']['token'])
        store.membership(owner, ids['room'], ids['worker'], {'can_read': True, 'can_post': True, 'can_request': False}, 0)
        for name in ('worker', 'reviewer'):
            store.placement(owner, ids['host'], 'SYNTHETIC private native host', 'isolated-proof', {}, ids[name])
        runner = web.AppRunner(application(store))
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', plan['port']).start()
        write(root / 'server-receipt.json', {'pid': os.getpid(), 'startTicks': ticks(os.getpid()),
              'url': plan['url'], 'source': str(SOURCE), 'domainCut': HOLLYWOOD})
        write(root / 'host-spec.json', {'authorityId': ids['authority'], 'hostId': ids['host'],
              'hollywoodUrl': plan['url'], 'administratorCredential': credentials['Owner'],
              'policy': host_policy()})
        await command(plan['commands']['hostEnroll'], 'host-enroll')
        controller = await launch(plan['commands']['controller'], 'host-controller')
        # Observe the controller's own status file through kernel events; no model poll.
        controller_dir = Path(f"/var/lib/harness/{ids['authority']}/hosts/{ids['host']}")
        await asyncio.to_thread(wait_socket, controller_dir, 'status.json', 30)
        require(json.loads((controller_dir / 'status.json').read_text())['state'] == 'healthy',
                'Original controller did not establish healthy capacity observation')

        async with ClientSession(timeout=ClientTimeout(total=20)) as client:
            async def api(path, body):
                async with client.post(plan['url'] + '/hollywood/v3/' + path, json=body,
                        headers={'Authorization': 'Bearer ' + credentials['Owner']['token']}) as response:
                    value = await response.json()
                    require(response.status == 200, f'Original operation rejected: {path} {response.status}')
                    with (root / 'proof/http-receipts.jsonl').open('a') as output:
                        output.write(json.dumps({'path': path, 'result': value}, default=str) + '\n')
                    return value

            # Synthetic installer metadata only: no delivery, executor, binding,
            # result or effect is inserted. Three subsequent deliveries are real.
            with store.pool.connection() as connection:
                source_id = store.event(connection, store.actor(connection, owner), ids['room'],
                    'request', 'SYNTHETIC local goal-source metadata; no real human order.',
                    recipient=ids['reviewer'], data={'syntheticBootstrapOnly': True})
            goal = await api(f"rooms/{ids['room']}/goals", {'operationId': str(uuid4()), 'id': ids['goal'],
                'sourceEventId': str(source_id), 'title': 'SYNTHETIC native report recovery',
                'objective': 'One real original effect, later report, independent native review',
                'acceptance': 'Exact native receipts and immutable original history', 'reviewBudget': 0})
            await api(f"goals/{ids['goal']}/work", {'operationId': str(uuid4()), 'id': ids['work'],
                'revision': goal['goalRevision'], 'roomId': ids['room'], 'ownerId': ids['worker'],
                'specification': original_prompt(), 'acceptance': 'Counter and artifact once, completed unreported', 'dependencies': []})
            assigned = await api(f"work/{ids['work']}/assign", {'operationId': str(uuid4()), 'revision': 1})
            original = assigned['deliveryId']

            async def phase(name, actor, delivery):
                reserve()
                env_file = root / (actor + '-claim.env')
                env_file.write_text('LAX_CLAIM_ID=' + plan['claims'][name] + '\n')
                # The namespace owns these exact pinned finite units, never live units.
                from lax_connector.service_observer import properties
                unit = plan['units'][actor]
                previous_invocation = properties(unit)['InvocationID']
                # Wait for controller-generated protected registration via inotify,
                # then record that exact invocation for cleanup even on failure.
                invocation = {'unit': unit, 'phase': name, 'registered': False}
                units.append(invocation)
                process = await launch(['systemctl', '--wait', 'start', unit], name + '-native')
                registry = Path(f"/var/lib/harness/{ids['authority']}/hosts/{ids['host']}/services/{ids[actor]}")
                try:
                    # Observe the current actual unit only; private namespace and
                    # preflight established no previous process. Never start again.
                    manifest_path = await asyncio.to_thread(wait_registered, registry, unit, previous_invocation, 45)
                    manifest = protected(manifest_path)
                    require(manifest['unit'] == unit, 'Registered service changed')
                    invocation.update({'registered': True, 'invocationId': manifest['serviceInvocation'],
                        'guardianPid': manifest['guardianPid'], 'guardianStart': manifest['guardianStart'],
                        'controlGroup': manifest['controlGroup']})
                    write(root / 'proof' / (name + '-guardian.json'), invocation)
                    require(await asyncio.wait_for(process.wait(), plan['limits']['perPhaseSeconds']) == 0,
                            f'{name} native guardian did not complete normally')
                finally:
                    # Ownership remains recorded if registration/start acknowledgement
                    # is missing. Cleanup refuses to invent the absent incarnation.
                    pass
                value = snapshot(original)
                assert_native_phase(value, delivery, ids[actor])
                write(root / 'proof' / (name + '.json'), value)
                return value

            before = await phase('original', 'worker', original)
            require(before['work']['state'] == 'assigned' and before['work']['report_event'] is None
                    and len(before['attempts']) == 1 and before['counter'] == '1\n',
                    'Original is not completed/unreported with effect once')
            artifact = json.loads((worker_home(ids['authority'], ids['worker']) / 'work/effect.json').read_text())
            context = before['workerContext'][0]
            require(all(artifact.get(key) == value for key, value in {
                'deliveryId': original, 'requestId': before['original']['event'],
                'contextId': context['context_id'], 'nativeThread': context['native_thread'],
                'hostId': ids['host'], 'counterSha256': before['artifacts'][0]['sha256']}.items()),
                'Original artifact does not contain actual captured identities/hash')
            later = await api(f"rooms/{ids['room']}/messages", {'operationId': str(uuid4()),
                'kind': 'request', 'body': later_prompt(ids['work'], original),
                'audience': 'room', 'recipientId': ids['worker']})
            current = later['deliveries'][0]['id']
            after = await phase('later', 'worker', current)
            assert_preserved(before, after)
            require(len(after['reconciliations']) == 1 and after['work']['state'] == 'submitted',
                    'Actual report reconciliation missing')
            audit = after['reconciliations'][0]
            require(audit['delivery'] == original and audit['current_delivery'] == current
                    and audit['principal'] == ids['worker'], 'Report lost original/current attribution')
            bindings = {row['delivery']: row for row in after['bindings']}
            require(all(bindings[original][key] == bindings[current][key] for key in
                        ('principal', 'context_id', 'native_thread', 'workspace_id')), 'Later context/thread/workspace changed')
            later_source = assert_tool(after, current, ids['worker'], 'hollywood_work_report_reconcile')
            require(audit['evidence']['currentParticipation'] == later_source,
                    'Reconciliation did not preserve exact later native source')
            actual_evidence = after['work']['evidence']
            require([{key: item[key] for key in ('relativePath', 'sha256', 'size')} for item in actual_evidence]
                    == before['artifacts'] and all(item['hostId'] == ids['host']
                        and item['workspaceId'] == bindings[original]['workspace_id'] for item in actual_evidence),
                    'Connector evidence differs from actual original bytes/scope')
            review = await api(f"rooms/{ids['room']}/messages", {'operationId': str(uuid4()),
                'kind': 'request', 'body': review_prompt(ids['work'], str(worker_home(ids['authority'], ids['worker']) / 'work')),
                'audience': 'room', 'recipientId': ids['reviewer']})
            final = await phase('review', 'reviewer', review['deliveries'][0]['id'])
            assert_preserved(before, final)
            require(final['work']['state'] == 'accepted' and len(final['deliveries']) == 3,
                    'Independent ordinary native acceptance missing')
            assert_tool(final, review['deliveries'][0]['id'], ids['reviewer'], 'hollywood_work_review')
            verified = {'state': 'passed', 'actualNativeProof': True,
                'originalDelivery': original, 'laterDelivery': current, 'reviewDelivery': review['deliveries'][0]['id'],
                'counter': 1, 'workAttempts': 1, 'proofSnapshots': ['original.json', 'later.json', 'review.json']}
            write(root / 'proof/verification.json', {**verified, 'state': 'verified_cleanup_pending'})
    except BaseException as error:
        write(root / 'proof/failure.json', {'state': 'held_or_failed', 'error': str(error),
              'effects': 'Preserved; uncertain operations are not re-admitted', 'actualNativeProof': False})
        raise
    finally:
        # Only this private namespace's pinned units and processes can be stopped.
        latest = {row['unit']: row for row in units}
        for unit, incarnation in latest.items():
            try:
                require(incarnation['registered'], 'Unknown original guardian; cleanup held')
                from lax_connector.service_observer import properties, process as observed_process, members
                current = properties(unit)
                require(current['InvocationID'] == incarnation['invocationId'], 'Service incarnation changed; no stop')
                await command(['systemctl', 'stop', unit], 'cleanup-' + unit,
                              timeout=plan['limits']['cleanupSeconds'])
                after = properties(unit)
                require(after['ActiveState'] == 'inactive' and after['MainPID'] == '0', 'Owned unit not inactive')
                guardian = observed_process(incarnation['guardianPid'])
                require((guardian is None or guardian['start'] != incarnation['guardianStart'])
                        and not members(incarnation['controlGroup']), 'Original owned guardian/group not drained')
                cleanup.append({**incarnation, 'stop': 'confirmed_inactive'})
            except Exception as error:
                cleanup.append({'unit': unit, 'stop': 'unconfirmed', 'error': str(error)})
        # Stop the controller before the HTTP server; stop PostgreSQL last.
        stopped = [row for row in reversed(owned) if row[1]['label'] != 'postgres']
        for process, record, log in stopped:
            if process.returncode is None:
                try:
                    require(record['startTicks'] is not None and ticks(process.pid) == record['startTicks'], 'Owned PID incarnation changed')
                    require(os.getpgid(process.pid) == process.pid, 'Owned command process group changed')
                    os.killpg(process.pid, signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), 20)
                except Exception as error:
                    cleanup.append({**record, 'stop': 'unconfirmed', 'error': str(error)})
            cleanup.append({**record, 'returncode': process.returncode,
                            'originalProcessStillPresent': process.returncode is None})
            log.close()
        if runner:
            try:
                await runner.cleanup()
                cleanup.append({'serverPid': os.getpid(), 'serverStartTicks': ticks(os.getpid()),
                                'applicationStopped': True})
            except Exception as error:
                cleanup.append({'applicationStopped': False, 'stop': 'unconfirmed', 'error': str(error)})
        if store:
            try:
                store.close()
            except Exception as error:
                cleanup.append({'storeClosed': False, 'stop': 'unconfirmed', 'error': str(error)})
        for process, record, log in (row for row in owned if row[1]['label'] == 'postgres'):
            if process.returncode is None:
                try:
                    require(ticks(process.pid) == record['startTicks'], 'Original PG incarnation changed')
                    process.send_signal(signal.SIGINT)
                    await asyncio.wait_for(process.wait(), 20)
                except Exception as error:
                    cleanup.append({**record, 'stop': 'unconfirmed', 'error': str(error)})
            cleanup.append({**record, 'returncode': process.returncode,
                            'originalProcessStillPresent': process.returncode is None})
            log.close()
        socket_dir = Path(config['host'])
        if socket_dir.exists() and not any(socket_dir.iterdir()):
            socket_dir.rmdir()
            cleanup.append({'ownedSocketDir': str(socket_dir), 'removedAfterStop': True})
        write(root / 'proof/cleanup.json', cleanup)
        require(not any(row.get('originalProcessStillPresent') or row.get('stop') == 'unconfirmed'
                        for row in cleanup), 'Owned cleanup incomplete; original evidence held')
        if verified:
            write(root / 'proof/result.json', verified)


def wait_socket(directory, filename, timeout):
    """Linux inotify wait; checks existing file after registering to close the race."""
    import ctypes
    import select
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    require(fd >= 0, 'inotify unavailable')
    try:
        require(libc.inotify_add_watch(fd, os.fsencode(directory), 0x100 | 0x80 | 0x8) >= 0,
                'Owned readiness directory unavailable')
        if (Path(directory) / filename).exists():
            return
        import time
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            require(left > 0 and select.select([fd], [], [], max(0, left))[0], 'Owned readiness deadline exceeded')
            os.read(fd, 65536)
            if (Path(directory) / filename).exists():
                return
    finally:
        os.close(fd)


def wait_postgres(directory, original_pid, timeout):
    """PostgreSQL's own postmaster.pid readiness, driven by kernel writes."""
    import ctypes
    import select
    import time
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    require(fd >= 0, 'inotify unavailable')
    try:
        require(libc.inotify_add_watch(fd, os.fsencode(directory), 0x100 | 0x80 | 0x8 | 0x2) >= 0,
                'Owned PostgreSQL directory unavailable')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fields = (Path(directory) / 'postmaster.pid').read_text().splitlines()
                if len(fields) >= 8 and fields[0] == str(original_pid) and fields[7].strip() == 'ready':
                    return
            except FileNotFoundError:
                pass
            left = deadline - time.monotonic()
            require(left > 0 and select.select([fd], [], [], max(0, left))[0],
                    'Original PostgreSQL readiness deadline exceeded')
            os.read(fd, 65536)
    finally:
        os.close(fd)


def wait_registered(directory, unit, previous_invocation, timeout):
    """Kernel event wait for a protected actual service incarnation, no model poll."""
    # The controller creates the agent directory while observing the reservation.
    # Watch its existing host parent first, then the new services and agent paths.
    directory = Path(directory)
    missing = []
    ancestor = directory
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    import time
    deadline = time.monotonic() + timeout
    for path in reversed(missing):
        wait_socket(path.parent, path.name, max(0, deadline - time.monotonic()))
    import ctypes
    import select
    from lax_connector.service_observer import properties
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    require(fd >= 0, 'inotify unavailable')
    try:
        require(libc.inotify_add_watch(fd, os.fsencode(directory), 0x100 | 0x80 | 0x8) >= 0,
                'Protected service registry unavailable')
        while True:
            for path in directory.glob('*.json'):
                manifest = protected(path)
                if (manifest.get('schema') == 'lax-service-incarnation/v1'
                        and manifest.get('serviceInvocation') != previous_invocation
                        and manifest.get('unit') == unit
                        and properties(unit)['InvocationID'] == manifest['serviceInvocation']):
                    return path
            left = deadline - time.monotonic()
            require(left > 0 and select.select([fd], [], [], max(0, left))[0],
                    'Original registered invocation deadline exceeded')
            os.read(fd, 65536)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check-runtime', 'run'))
    parser.add_argument('--base', type=Path, default=ARTIFACTS)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--namespace-capability', type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == 'prepare':
            root, plan = prepare(args.base)
            print(json.dumps({'state': plan['state'], 'root': str(root), 'servicesStarted': 0,
                              'nativeAdmissions': 0, 'missingCapability': MISSING}))
            return 0
        require(args.plan is not None, '--plan required')
        plan = json.loads(args.plan.read_text())
        require(Path(plan['root']).resolve() == args.plan.resolve().parent, 'Plan root mismatch')
        cap = check_capability(plan, args.admission, args.namespace_capability)
        if args.action == 'run':
            asyncio.run(run(plan, cap))
        else:
            print(json.dumps({'state': 'runtime_gate_checked_no_start', 'nativeAdmissions': 0}))
        return 0
    except Held as error:
        print(json.dumps({'state': 'held', 'reason': str(error), 'noRetryAuthorized': True}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
