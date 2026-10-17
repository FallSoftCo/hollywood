"""Run only the narrow claim primitive against ONE fresh RAM PostgreSQL/HTTP fixture.

Existing dependencies only. No native/controller/model process is started.
"""
import getpass
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
AREA = Path('/dev/shm/lax-native-fork-global-20261017-r01/hollywood-owned')
PYTHON = '/opt/harness/production-core-20261016-r03/venv/bin/python'
DEPENDENCY = Path('/dev/shm/harness-completed-report-recovery-20261016-r01/shared/pg-dependency-route-r01.json')


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('claims', 'edit-plans', 'report-scope', 'delivery-edits', 'resource-routing', 'placement-control'), default='claims')
    args = parser.parse_args()
    os.umask(0o077)
    fs = os.statvfs('/aosp')
    free = fs.f_bavail * fs.f_frsize
    if free < 80 * 1024**3:
        raise RuntimeError('80GiB hard reserve unmet')
    area = AREA / ('placement-r01' if args.scenario == 'placement-control' else 'resource-r01' if args.scenario == 'resource-routing' else 'ordinary-r01' if args.scenario == 'delivery-edits' else 'r04' if args.scenario == 'report-scope' else 'r03')
    area.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='fresh-report-scope-' if args.scenario == 'report-scope' else 'fresh-plans-' if args.scenario == 'edit-plans' else 'fresh-claims-', dir=area))
    tmp = root / 'tmp'
    tmp.mkdir()
    sock = Path(tempfile.mkdtemp(prefix='.c-', dir=SOURCE))
    dependency = json.loads(DEPENDENCY.read_text())
    binary = Path(dependency['bindir'])
    config = {'host': str(sock), 'port': 19372, 'dbname': 'canonical_collaboration_fixture', 'user': getpass.getuser()}
    write(root / 'database.json', config)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': str(tmp),
           'PYTHONPATH': str(SOURCE), 'LD_LIBRARY_PATH': dependency['libraryDir'],
           'CANONICAL_TEST_DATABASE': str(root / 'database.json'), 'CANONICAL_TEST_OUTPUT': str(root)}
    lifecycle = {'root': str(root), 'socket': str(sock), 'availableBytesBefore': free,
                 'nativeStarts': 0, 'controllerStarts': 0, 'commands': []}
    pid, token = None, None

    def ticks(identity):
        return Path(f'/proc/{identity}/stat').read_text().split(') ', 1)[1].split()[19]

    def run(argv, label, timeout=45):
        lifecycle['commands'].append({'label': label, 'argv': argv})
        with (root / (label + '.log')).open('x') as output:
            result = subprocess.run(argv, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'{label} failed: {result.returncode}; original log retained')

    try:
        run([str(binary / 'initdb'), '-D', str(root / 'pgdata'), '-U', config['user'],
             '--no-locale', '-E', 'UTF8', '--auth=trust', '-L', dependency['shareDir']], 'initdb')
        options = f"-h '' -k {sock} -p {config['port']} -F -c shared_buffers=16MB -c max_connections=20 -c unix_socket_permissions=0700"
        run([str(binary / 'pg_ctl'), '-D', str(root / 'pgdata'), '-l', str(root / 'postgres.log'),
             '-o', options, '-w', 'start'], 'start')
        pid = int((root / 'pgdata/postmaster.pid').read_text().splitlines()[0])
        token = ticks(pid)
        lifecycle.update(pid=pid, startTicks=token)
        run([str(binary / 'createdb'), '-h', str(sock), '-p', str(config['port']), '-U', config['user'], config['dbname']], 'createdb')
        test = 'integration_placement_control.py' if args.scenario == 'placement-control' else 'integration_resource_routing.py' if args.scenario == 'resource-routing' else 'integration_delivery_edits.py' if args.scenario == 'delivery-edits' else 'integration_report_scope.py' if args.scenario == 'report-scope' else 'integration_edit_plans.py' if args.scenario == 'edit-plans' else 'integration_canonical_collaboration.py'
        run([PYTHON, str(SOURCE / 'tests' / test)], 'integration')
        write(root / 'status.json', {'state': 'passed', 'nativeProof': False})
    except BaseException as error:
        write(root / 'status.json', {'state': 'failed_or_held', 'error': str(error), 'nativeProof': False})
        raise
    finally:
        pidfile = root / 'pgdata/postmaster.pid'
        if pid is None and pidfile.exists():
            candidate = int(pidfile.read_text().splitlines()[0])
            process = Path(f'/proc/{candidate}')
            if (process.exists() and process.stat().st_uid == os.getuid()
                    and str(root / 'pgdata').encode() in (process / 'cmdline').read_bytes()):
                pid, token = candidate, ticks(candidate)
                lifecycle.update(pid=pid, startTicks=token, startupAcknowledgementFailed=True)
        if pid is not None:
            original = Path(f'/proc/{pid}')
            if original.exists() and ticks(pid) == token:
                with (root / 'stop.log').open('x') as output:
                    stopped = subprocess.run([str(binary / 'pg_ctl'), '-D', str(root / 'pgdata'),
                        '-m', 'fast', '-w', 'stop'], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=45)
                lifecycle['stopReturnCode'] = stopped.returncode
            lifecycle['originalProcessStillPresent'] = original.exists() and ticks(pid) == token
        else:
            lifecycle['databaseStarted'] = False
        if sock.exists() and not any(sock.iterdir()):
            sock.rmdir()
            lifecycle['ownedSocketRemoved'] = True
        write(root / 'cleanup.json', lifecycle)
        print(json.dumps({'root': str(root), 'cleanup': lifecycle.get('stopReturnCode'),
                          'originalProcessStillPresent': lifecycle.get('originalProcessStillPresent', False)}))
        if lifecycle.get('originalProcessStillPresent') or lifecycle.get('stopReturnCode', 0) != 0:
            raise RuntimeError('Original owned PostgreSQL cleanup remains unconfirmed')


if __name__ == '__main__':
    main()
