"""Prepare a new RAM fixture; optionally run focused PG/HTTP tests, never models.

The separate real-native proof remains gated on compatible commits/cross-review.
No retained fixture service or production database is contacted by this driver.
"""
import argparse
import getpass
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path

BASE = Path(os.environ.get('RECOVERY_ARTIFACT_ROOT', '/dev/shm/harness-completed-report-recovery-20261016-r01/artifacts-hollywood/implementation-r02'))
RESERVE = 80 * 1024**3
HEADROOM = 48 * 1024**2
PYTHON = '/opt/harness/production-core-20261016-r03/venv/bin/python'
NATIVE = '/opt/harness/production-core-20261016-r03/native/bin/codex'


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def prepare():
    os.umask(0o077)
    available = os.statvfs('/aosp')
    free = available.f_bavail * available.f_frsize
    if free < RESERVE + HEADROOM:
        raise RuntimeError(f'/aosp phase reserve unmet: {free} bytes available')
    BASE.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='fresh-harness-', dir=BASE))
    for name in ('tmp', 'workspace-worker', 'workspace-reviewer', 'states'):
        (root / name).mkdir(mode=0o700)
    # PostgreSQL's Unix socket path is limited to108 bytes. Keep socket files
    # inside this owned RAM worktree, away from long evidence-directory names.
    socket_dir = Path(tempfile.mkdtemp(prefix='.s-', dir=Path(__file__).resolve().parents[1]))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config = {'host': str(socket_dir), 'port': port,
              'dbname': 'report_recovery_fixture', 'user': getpass.getuser()}
    write(root / 'database.json', config)
    plan = {
        'schema': 'completed-report-new-isolated-harness/v1', 'state': 'prepared_no_native_execution',
        'root': str(root), 'reserveBytes': RESERVE, 'observedAospAvailableBytes': free,
        'python': PYTHON, 'existingNativeBinary': NATIVE,
        'database': config, 'credentials': 'Fresh synthetic fixture authority only; no production database config or credentials.',
        'provider': 'Later native driver must use approved existing account route in place; never copy credentials or repurpose HOME/CODEX_HOME.',
        'focusedHTTPCommand': [PYTHON, str(Path(__file__).with_name('integration_completed_report_reconciliation.py'))],
        'focusedLabel': 'Actual PG/HTTP, synthetic execution receipts, zero models; not the native proof.',
        'nativeGate': {
            'requires': ['Exact compatible Hollywood and LAX commits', 'First independent LAX review of server/harness',
                         'Explicit Global admission', 'New isolated host-controller/execution enrollment',
                         'Approved existing native provider route and current effect policy'],
            'automaticNativeAdmission': False,
        },
        'nativeSequence': [
            {'phase': 'original', 'action': 'New synthetic authority/room/goal/work in this isolated stack. Admit exactly one real native worker assignment; write counter/evidence in workspace-worker and finish without work report.',
             'assert': 'Original delivery completed, original effect counter exactly1, original resource independently stopped, assignment still unreported. Preserve request/result/action/context/nativeTurn IDs.'},
            {'phase': 'later', 'action': 'New normal native participation for the same worker, same persistent context/thread/host/workspace. Read original work briefing/effect receipts and invoke hollywood_work_report_reconcile with actual original artifact paths.',
             'assert': 'Real dynamic tool/action and native-source v2 audit use the later delivery. Report receipt references original work/delivery/attempt; same context, no effect counter increment, no new attempt or original model start.'},
            {'phase': 'review', 'action': 'One distinct authorized real native manager/reviewer inspects original artifact bytes and native reconciliation receipt, then uses ordinary work.review.',
             'assert': 'Report precedes independent acceptance; reviewer differs from worker. Original assignment, delivery, context, bindings, effect rows and counter stay unchanged.'},
            {'phase': 'close', 'action': 'Stop only this new isolated stack and retain RAM IDs/logs/hashes. Independently compare production service/source baseline.',
             'assert': 'No production/history replay or provider-effect ambiguity resolved by assumption.'},
        ],
        'nativeReadiness': 'Native plan and fresh workspace/state paths prepared. Actual controller/connector native driver is intentionally not launched or claimed qualified in this initial server cut; it must be completed against the compatible reviewed LAX cut.',
    }
    write(root / 'harness-plan.json', plan)
    return root, config


def focused(root, config, bindir, library_dir, share_dir):
    commands = []
    started = False
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': str(root / 'tmp'),
           'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
           'RECOVERY_EVIDENCE': str(root / 'http-result'),
           'RECOVERY_DATABASE_CONFIG': str(root / 'database.json')}
    if library_dir:
        env['LD_LIBRARY_PATH'] = str(library_dir)
    lifecycle = {'root': str(root), 'dataDir': str(root / 'pgdata'), 'nativeStarted': False,
                 'libraryDir': str(library_dir), 'shareDir': str(share_dir), 'commands': commands}
    try:
        for name in ('initdb', 'pg_ctl', 'createdb'):
            path = bindir / name
            if not os.access(path, os.X_OK):
                raise RuntimeError(f'Existing PostgreSQL dependency not executable as UID{os.getuid()}: {path}')

        def run(argv):
            commands.append(argv)
            with (root / 'commands.log').open('a') as output:
                result = subprocess.run(argv, env=env, stdout=output, stderr=subprocess.STDOUT,
                                        timeout=60, check=False)
            if result.returncode:
                raise RuntimeError(f'Owned fixture command exited{result.returncode}: {argv[0]}')

        run([str(bindir / 'initdb'), '-D', str(root / 'pgdata'), '--no-locale', '-E', 'UTF8',
             '-U', config['user'], '--auth=trust'] + (['-L', str(share_dir)] if share_dir else []))
        options = f"-h '' -k {config['host']} -p {config['port']} -F -c shared_buffers=16MB -c max_connections=20 -c unix_socket_permissions=0700"
        run([str(bindir / 'pg_ctl'), '-D', str(root / 'pgdata'), '-l', str(root / 'postgres.log'),
             '-o', options, '-w', 'start'])
        started = True
        pid = int((root / 'pgdata' / 'postmaster.pid').read_text().splitlines()[0])
        lifecycle['postmasterPid'] = pid
        lifecycle['originalStartTicks'] = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19]
        run([str(bindir / 'createdb'), '-h', config['host'], '-p', str(config['port']),
             '-U', config['user'], config['dbname']])
        run([PYTHON, str(Path(__file__).with_name('integration_completed_report_reconciliation.py'))])
        write(root / 'focused-status.json', {'state': 'passed', 'commands': commands,
              'actualPostgreSQL': True, 'actualNativeProof': False})
    except Exception as error:
        write(root / 'focused-status.json', {'state': 'blocked_or_failed', 'error': str(error),
              'commands': commands, 'actualNativeProof': False})
        raise
    finally:
        pid_file = root / 'pgdata' / 'postmaster.pid'
        if not started and pid_file.exists():
            # A failed start acknowledgement is not proof that nothing started.
            candidate = int(pid_file.read_text().splitlines()[0])
            process = Path(f'/proc/{candidate}')
            if (process.exists() and process.stat().st_uid == os.getuid()
                    and str(root / 'pgdata').encode() in (process / 'cmdline').read_bytes()):
                started = True
                lifecycle['postmasterPid'] = candidate
                lifecycle['originalStartTicks'] = (process / 'stat').read_text().split(') ', 1)[1].split()[19]
                lifecycle['startupAcknowledgementFailed'] = True
        if started:
            with (root / 'shutdown.log').open('a') as output:
                stopped = subprocess.run([str(bindir / 'pg_ctl'), '-D', str(root / 'pgdata'), '-m', 'fast', '-w', 'stop'],
                                         env=env, stdout=output, stderr=subprocess.STDOUT, timeout=60, check=False)
                lifecycle['stopReturnCode'] = stopped.returncode
            stat = Path(f"/proc/{lifecycle['postmasterPid']}/stat")
            lifecycle['originalProcessStillPresent'] = stat.exists() and stat.read_text().split(') ', 1)[1].split()[19] == lifecycle['originalStartTicks']
            write(root / 'cleanup-receipt.json', lifecycle)
            if stopped.returncode or lifecycle['originalProcessStillPresent']:
                raise RuntimeError('Owned PostgreSQL cleanup not confirmed; inspect exact cleanup receipt')
        else:
            lifecycle['databaseStarted'] = False
            write(root / 'cleanup-receipt.json', lifecycle)
        socket_dir = Path(config['host'])
        if socket_dir.exists() and not any(socket_dir.iterdir()):
            socket_dir.rmdir()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--focused', action='store_true')
    parser.add_argument('--pg-bindir', type=Path)
    parser.add_argument('--pg-library-dir', type=Path)
    parser.add_argument('--pg-share-dir', type=Path)
    args = parser.parse_args()
    root, config = prepare()
    print(json.dumps({'preparedRoot': str(root), 'nativeStarted': False}), flush=True)
    if args.focused:
        if not args.pg_bindir:
            raise SystemExit('--focused requires an explicitly approved existing --pg-bindir')
        focused(root, config, args.pg_bindir, args.pg_library_dir, args.pg_share_dir)
