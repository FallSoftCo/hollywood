"""Additive Windows API pin registration; never install, start, enable or stop.

This adapter consumes reviewed immutable code and the ORIGINAL task acceptance.
Its component lock fences cooperating operations, not a hostile current owner.
Activation, graceful drain, health and rollback execution remain separate.
"""
import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import msvcrt
import ntpath
from pathlib import Path
import runpy
import stat
import subprocess
import time
from xml.etree import ElementTree as ET
import zipfile

import win32api
import win32con
import win32security
import win32com.client
from lax_connector.windows_ipc import create_private_directory, private_path

NORMALIZER_SHA = '10c274e823fdd86ab50a0f800952532c543e9c1d1e65f68c6e0f4a037a8f9b0a'
PRODUCT_TASK = 'LaxHost-BLD-11a7ef1b-Hollywood-r01'
QA_TASK = 'LaxApiComponent-QA-39f94f8f-r01'


class Held(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Held(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def exclusive(path, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, indent=2).encode()+b'\n'
    with Path(path).open('xb') as f:
        f.write(raw)
        f.flush()
        import os
        os.fsync(f.fileno())
    private_path(path)


def read(path, sha=None, private=False, limit=64*1024*1024):
    p = Path(path)
    require(p.is_absolute() and p.resolve() == p, 'Path is not exact/absolute or crosses an alias')
    s = p.lstat()
    require(stat.S_ISREG(s.st_mode) and not s.st_file_attributes & 0x400 and s.st_size <= limit,
            'File is reparse/nonregular/oversize')
    if private:
        private_path(p)
    raw = p.read_bytes()
    require(sha is None or digest(raw) == sha, 'Immutable file pin differs')
    return raw


def current_owner():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        require(not win32security.GetTokenInformation(token, win32security.TokenElevation), 'Elevated caller refused')
        require(win32security.GetTokenInformation(token, win32security.TokenSessionId) == 1, 'Session1 required')
        return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()


def sid(value):
    s = win32security.ConvertStringSidToSid(value) if value.startswith('S-') else win32security.LookupAccountName(None, value)[0]
    return win32security.ConvertSidToStringSid(s)


def scheduler():
    s = win32com.client.Dispatch('Schedule.Service')
    s.Connect()
    return s.GetFolder('\\')


def xml_identity(raw, text=False):
    if isinstance(raw, bytes):
        # Scheduler returns a Unicode XML string declaring UTF-16; receipts
        # deliberately store that string as UTF-8 bytes with its original text.
        raw = raw.decode('utf-8')
    root = ET.fromstring(raw)
    settings_defaults = {'AllowStartOnDemand': 'true', 'RunOnlyIfIdle': 'false',
        'AllowHardTerminate': 'true', 'Priority': '7', 'WakeToRun': 'false',
        'StartWhenAvailable': 'false', 'RunOnlyIfNetworkAvailable': 'false'}
    for parent in root.iter():
        parent_name = parent.tag.rsplit('}', 1)[-1]
        for node in list(parent):
            name = node.tag.rsplit('}', 1)[-1]
            # COM emits explicit defaults in Definition.XmlText, but omits them
            # from RegisteredTask.Xml. Compare precisely those known defaults.
            if (parent_name == 'Settings' and name in settings_defaults
                    and node.text == settings_defaults[name]):
                parent.remove(node)
            elif parent_name == 'Principal' and name == 'RunLevel' and node.text == 'LeastPrivilege':
                parent.remove(node)
            elif parent_name == 'RegistrationInfo' and name == 'SecurityDescriptor':
                # Actual owner/group/DACL is independently bound and compared.
                parent.remove(node)
    for node in root.iter():
        name = node.tag.rsplit('}', 1)[-1]
        if name == 'UserId' and node.text:
            node.text = sid(node.text)
        if name in ('Command', 'WorkingDirectory') and node.text:
            node.text = ntpath.normcase(ntpath.normpath(node.text))
    # Scheduler changes order of named XML properties. Action/trigger lists
    # remain ordered and untouched; unknown containers are not canonicalized.
    property_containers = {'Task', 'RegistrationInfo', 'Settings', 'Principal',
                           'IdleSettings', 'NetworkSettings', 'Exec', 'Repetition', 'MaintenanceSettings'}
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] in property_containers:
            names = [child.tag for child in node]
            require(len(names) == len(set(names)), 'Duplicate task property rejected')
            node[:] = sorted(node, key=lambda child: child.tag)
    normalized = ET.canonicalize(ET.tostring(root, encoding='unicode'), strip_text=True)
    return normalized if text else digest(normalized.encode())


def task_snapshot(task, normalize):
    return {'normalized': normalize(task.Definition), 'xmlIdentity': xml_identity(task.Definition.XmlText),
            'xmlSha256': digest(task.Xml.encode()), 'sddl': task.GetSecurityDescriptor(7),
            'enabled': bool(task.Enabled), 'state': task.State, 'instances': task.GetInstances(0).Count}


def task_key(snapshot):
    return {k: snapshot[k] for k in ('normalized', 'xmlIdentity', 'sddl', 'enabled')}


def normalizer(spec):
    path = Path(spec['normalizer'])
    read(path, NORMALIZER_SHA)
    return runpy.run_path(str(path))['definition_identity']


def bind(spec):
    require(spec['schema'] == 'hollywood-api-component-windows/v1', 'Schema differs')
    require(spec['component'] in (PRODUCT_TASK, QA_TASK), 'Not the dedicated reviewed BLD API/fixture task')
    owner = current_owner()
    original = json.loads(read(spec['originalSpecFile'], private=True, limit=65536))
    installed = json.loads(read(spec['installationFile'], spec['installationSha256'], private=True, limit=65536))
    require(original['role'] == 'hollywood' and original['taskName'] == spec['component'], 'Original API binding differs')
    require(installed['taskName'] == spec['component'], 'Original installation task binding differs')
    require(digest(canonical(original)) == installed['specSha256'] == spec['originalSpecSha256'], 'Original accepted spec differs')
    require(Path(spec['installationFile']).parent == Path(original['recordDirectory']), 'Original acceptance directory differs')
    require(original['command'][0].lower().endswith('pythonw.exe'), 'Existing interpreter differs')
    read(original['command'][0], spec['interpreterSha256'])
    require(spec['from']['launcher'] != spec['to']['launcher'], 'Distinct successor path required')
    for side in ('from', 'to'):
        for kind in ('launcher', 'package'):
            read(spec[side][kind], spec[side][kind+'Sha256'], private=side == 'to')
            if side == 'to':
                require(Path(spec[side][kind]).stat().st_nlink == 1, 'Successor cannot share capsule hardlinks')
                require(not Path(spec[side][kind]).is_relative_to(Path(spec['from']['launcher']).parent),
                        'Successor must be outside original capsule directory')
    require(1 <= len(spec['dependencyPins']) <= 32, 'Dependency manifest pins required')
    for pin in spec['dependencyPins']:
        read(pin['path'], pin['sha256'])
    with zipfile.ZipFile(io.BytesIO(read(spec['to']['package'], spec['to']['packageSha256'], private=True))) as z:
        entries = z.infolist()
        require(len(entries) <= 10000 and sum(e.file_size for e in entries) <= 128*1024*1024, 'Package budget')
        seen = set()
        for e in entries:
            name = e.filename
            require(name.casefold() not in seen and not name.startswith('/') and '\\' not in name
                    and ':' not in name and '..' not in name.split('/')
                    and not stat.S_ISLNK(e.external_attr >> 16), 'Unsafe package member')
            seen.add(name.casefold())
        require(z.testzip() is None, 'Package CRC differs')
    return original, installed, owner


def component_root(original):
    # Derived from accepted original, never chosen separately by each operation.
    return Path(original['recordDirectory'])/'api-component-maintenance'


def accepted_command(spec, original, seen=()):
    """Resolve ONLY original acceptance plus a sealed accepted predecessor chain.

    Return this operation's from-command and the predecessor's exact accepted
    successor task identity. No adoption of an arbitrary current action.
    """
    predecessor = spec.get('predecessor')
    if predecessor is None:
        command = original['command']
        require(command.count(spec['from']['launcher']) == 1, 'First launcher must match ORIGINAL accepted command')
        return command, None
    require(set(predecessor) == {'operation', 'planSha256', 'acceptanceSha256'}, 'Invalid predecessor binding')
    previous = Path(predecessor['operation'])
    require(str(previous) not in seen and len(seen) < 32, 'Predecessor cycle/depth held')
    private_path(previous, directory=True)
    raw = read(previous/'plan.json', predecessor['planSha256'], private=True, limit=2*1024*1024)
    plan = json.loads(raw)
    require(plan.get('planFormatVersion') == 2, 'Predecessor requires accepted chain-aware plan')
    old = plan['spec']
    for name in ('schema', 'component', 'normalizer', 'originalSpecFile', 'originalSpecSha256',
                 'installationFile', 'installationSha256', 'interpreterSha256', 'dependencyPins'):
        require(old[name] == spec[name], 'Predecessor changes original acceptance/dependency boundary')
    require(spec['from'] == old['to'], 'From pins are not the accepted predecessor successor')
    require((previous/'registration-accepted.json').exists(), 'Predecessor dispatch is unresolved')
    accepted = json.loads(read(previous/'registration-accepted.json', predecessor['acceptanceSha256'], private=True))
    intent = json.loads(read(previous/'registration-intent.json', private=True))
    require(accepted['state'] == 'successor-definition' and accepted['originalIntentRetained'] is True
            and intent['successorXmlSha256'] == plan['successorXmlSha256'], 'Predecessor not accepted')
    root = component_root(original)
    require(Path(plan['componentRoot']) == root, 'Predecessor component lock differs')
    entry = root/'registrations'/(digest(str(previous).encode())+'.accepted.json')
    require(entry.exists(), 'Predecessor component acceptance missing')
    ledger = json.loads(read(entry, private=True))
    require(ledger['operation'] == str(previous) and ledger['planSha256'] == digest(raw)
            and ledger['observedXmlSha256'] == accepted['xmlSha256'], 'Predecessor ledger differs')
    prior_command, _ = accepted_command(old, original, (*seen, str(previous)))
    require(prior_command.count(old['from']['launcher']) == 1, 'Predecessor command is ambiguous')
    command = [old['to']['launcher'] if arg == old['from']['launcher'] else arg for arg in prior_command]
    for side in ('from', 'to'):
        for kind in ('launcher', 'package'):
            read(old[side][kind], old[side][kind+'Sha256'])
    successor_xml = read(previous/'task.successor.xml', plan['successorXmlSha256'], private=True)
    expected = {**plan['successor'], 'xmlIdentity': xml_identity(successor_xml)}
    return command, {'taskKey': expected, 'observedXmlSha256': accepted['xmlSha256']}


@contextmanager
def component_lock(root, create=False):
    if create:
        create_private_directory(root)
    private_path(root, directory=True)
    p = root/'component.lock'
    if create:
        try:
            exclusive(p, b'\0')
        except FileExistsError:
            pass
    # Windows byte locks are mandatory for reads as well. Inspect ACL/type/size
    # without reading the byte that a cooperating process may already lock.
    private_path(p)
    require(p.stat().st_size == 1, 'Component lock size differs')
    with p.open('r+b') as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise Held('Component lock is held by another original operation') from e
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def validate_task(task, original, owner, command):
    d = task.Definition
    require(sid(d.Principal.UserId) == owner and d.Principal.LogonType == 3 and d.Principal.RunLevel == 0,
            'Limited original interactive owner differs')
    require(d.Actions.Count == 1 and d.Actions.Item(1).Type == 0, 'Dedicated single Exec action required')
    a = d.Actions.Item(1)
    require(ntpath.normcase(ntpath.normpath(a.Path)) == ntpath.normcase(ntpath.normpath(command[0]))
            and a.Arguments == subprocess.list2cmdline(command[1:])
            and ntpath.normcase(ntpath.normpath(a.WorkingDirectory)) == ntpath.normcase(ntpath.normpath(original['cwd'])),
            'Task execution is not the original accepted command')


def prepare(spec, operation):
    original, installed, owner = bind(spec)
    operation = Path(operation)
    require(operation.is_absolute() and operation.resolve() == operation, 'Aliased operation')
    normalize = normalizer(spec)
    with component_lock(component_root(original), create=True):
        history_check(component_root(original), operation)
        if operation.exists():
            previous = load(operation)
            require(previous['spec'] == spec, 'Original operation cannot change specification')
            return {**observe(previous), 'preparedReconciled': True}
        task = scheduler().GetTask(spec['component'])
        command, predecessor = accepted_command(spec, original)
        validate_task(task, original, owner, command)
        before = task_snapshot(task, normalize)
        if predecessor is not None:
            require(task_key(before) == predecessor['taskKey']
                    and before['xmlSha256'] == predecessor['observedXmlSha256'],
                    'Live task is not the exact accepted predecessor successor')
        require(before['xmlSha256'] == spec['liveXmlSha256'] and digest(before['sddl'].encode()) == spec['taskAclSha256'],
                'Reviewed live task/XML/ACL snapshot differs')
        d = task.Definition  # detached COM definition; no manager write
        require(command.count(spec['from']['launcher']) == 1, 'Current accepted launcher is ambiguous')
        successor_command = [spec['to']['launcher'] if a == spec['from']['launcher'] else a for a in command]
        args = successor_command[1:]
        d.Actions.Item(1).Arguments = subprocess.list2cmdline(args)
        successor = {**task_key(before), 'normalized': normalize(d), 'xmlIdentity': xml_identity(d.XmlText)}
        create_private_directory(operation)
        exclusive(operation/'task.original.xml', task.Xml.encode())
        exclusive(operation/'task.original.definition.xml', task.Definition.XmlText.encode())
        exclusive(operation/'task.successor.xml', d.XmlText.encode())
        exclusive(operation/'plan.json', {'schema': spec['schema'], 'spec': spec,
            'planFormatVersion': 2, 'acceptedCommand': command, 'successorCommand': successor_command,
            'original': before, 'successor': successor, 'componentRoot': str(component_root(original)),
            'originalDefinitionSha256': digest(task.Definition.XmlText.encode()),
            'successorXmlSha256': digest(d.XmlText.encode()), 'activationDispatched': False})
        return {'state': 'prepared', 'registrationDispatched': False, 'activationDispatched': False}


def load(operation):
    private_path(operation, directory=True)
    plan = json.loads(read(Path(operation)/'plan.json', private=True, limit=2*1024*1024))
    original, _, _ = bind(plan['spec'])
    command, _ = accepted_command(plan['spec'], original)
    require(plan.get('planFormatVersion') == 2 and plan['acceptedCommand'] == command,
            'Accepted command/plan format differs')
    require(plan['successorCommand'] == [plan['spec']['to']['launcher'] if a == plan['spec']['from']['launcher'] else a for a in command],
            'Successor command differs from accepted predecessor chain')
    require(Path(plan['componentRoot']) == component_root(original), 'Stable component lock binding differs')
    old = read(Path(operation)/'task.original.xml', plan['original']['xmlSha256'], private=True)
    if 'originalDefinitionSha256' in plan:
        old = read(Path(operation)/'task.original.definition.xml', plan['originalDefinitionSha256'], private=True)
    new = read(Path(operation)/'task.successor.xml', plan['successorXmlSha256'], private=True)
    # Derive semantics from sealed bytes rather than treating a previous
    # serializer's hash representation as a different task policy.
    plan['original']['xmlIdentity'] = xml_identity(old)
    plan['successor']['xmlIdentity'] = xml_identity(new)
    return plan


def observe(plan):
    task = scheduler().GetTask(plan['spec']['component'])
    now = task_snapshot(task, normalizer(plan['spec']))
    key = task_key(now)
    if key == task_key(plan['original']):
        state = 'original-definition'
    elif key == plan['successor']:
        state = 'successor-definition'
    else:
        raise Held('Actual task definition/ACL/policy differs from both original and successor')
    return {'state': state, 'taskState': now['state'], 'instances': now['instances'],
            'enabled': now['enabled'], 'xmlSha256': now['xmlSha256'],
            'registrationDispatched': False, 'activationDispatched': False,
            'serviceHealth': 'not-observed'}


def inspect(operation):
    plan = load(operation)
    with component_lock(Path(plan['componentRoot'])):
        return observe(load(operation))


def _register(folder, name, definition, owner, sddl):
    # UPDATE only; suppress registration triggers and automatic principal ACEs.
    return folder.RegisterTaskDefinition(name, definition, 4 | 16 | 32, owner, None, 3, sddl)


def history_check(root, operation):
    """Unresolved original dispatch also fences a DIFFERENT operation."""
    history = root/'registrations'
    if not history.exists():
        return
    private_path(history, directory=True)
    intents = list(history.glob('*.intent.json'))
    require(len(intents) <= 1024, 'Component history requires reviewed archival')
    for path in intents:
        item = json.loads(read(path, private=True, limit=65536))
        accepted = path.with_name(path.name.replace('.intent.json', '.accepted.json'))
        if accepted.exists():
            value = json.loads(read(accepted, private=True, limit=65536))
            require(value['operation'] == item['operation'] and value['planSha256'] == item['planSha256'],
                    'Component history acceptance differs')
        elif item['operation'] != str(operation):
            raise Held('Another original registration is unresolved; inspect it, never create a replacement operation')


def history_record(root, operation, accepted=None):
    history = root/'registrations'
    create_private_directory(history)
    name = digest(str(operation).encode())
    value = {'operation': str(operation), 'planSha256': digest(read(operation/'plan.json', private=True))}
    intent = history/(name+'.intent.json')
    if intent.exists():
        require(json.loads(read(intent, private=True)) == value, 'Original component history binding differs')
    else:
        exclusive(intent, value)
    if accepted is not None:
        result = history/(name+'.accepted.json')
        if not result.exists():
            exclusive(result, {**value, 'observedXmlSha256': accepted['xmlSha256']})


def commit(operation, quiet_boundary, quiet_sha256):
    operation = Path(operation)
    plan = load(operation)
    with component_lock(Path(plan['componentRoot'])):
        plan = load(operation)
        # Revalidate code/original acceptance while holding the component lock.
        original, _, owner = bind(plan['spec'])
        history_check(Path(plan['componentRoot']), operation)
        observed = observe(plan)
        intent = operation/'registration-intent.json'
        acceptance = operation/'registration-accepted.json'
        if intent.exists():
            history_record(Path(plan['componentRoot']), operation)
            recorded = json.loads(read(intent, private=True))
            require(recorded['successorXmlSha256'] == plan['successorXmlSha256'], 'Original intent differs')
            require(observed['state'] == 'successor-definition',
                    'Original dispatch is uncertain or deliberately rolled back; never register again')
            if not acceptance.exists():
                exclusive(acceptance, {**observed, 'reconciled': True, 'originalIntentRetained': True})
            else:
                read(acceptance, private=True)
            history_record(Path(plan['componentRoot']), operation, observed)
            return {**observed, 'reconciled': True}
        require(not acceptance.exists() and observed['state'] == 'original-definition', 'Stale operation needs original inspection')
        ledger = Path(plan['componentRoot'])/'registrations'/(digest(str(operation).encode())+'.intent.json')
        require(not ledger.exists(), 'Original claim interrupted before operation intent; inspect, never replay')
        boundary = json.loads(read(quiet_boundary, quiet_sha256, private=True, limit=65536))
        require(boundary['schema'] == 'hollywood-api-quiet-boundary/v1' and boundary['approved'] is True
                and boundary['component'] == plan['spec']['component']
                and boundary['originalXmlSha256'] == observed['xmlSha256']
                and boundary['operation'] == str(operation)
                and boundary['planSha256'] == digest(read(operation/'plan.json', private=True))
                and boundary['successorXmlSha256'] == plan['successorXmlSha256']
                and 0 <= time.time()-boundary['approvedAt'] <= 300, 'Fresh exact owner quiet boundary required')
        require(observed['instances'] == 0 and observed['taskState'] in (1, 3), 'Original task must be stopped/quiet')
        folder = scheduler()
        d = folder.GetTask(plan['spec']['component']).Definition
        d.XmlText = read(operation/'task.successor.xml', plan['successorXmlSha256'], private=True).decode()
        history_record(Path(plan['componentRoot']), operation)
        exclusive(intent, {'successorXmlSha256': plan['successorXmlSha256'],
            'originalXmlSha256': observed['xmlSha256'], 'quietBoundarySha256': quiet_sha256,
            'activationDispatched': False, 'at': time.time()})
        final = observe(plan)
        require(final['state'] == 'original-definition' and final['instances'] == 0
                and final['taskState'] in (1, 3) and final['xmlSha256'] == observed['xmlSha256'],
                'Task changed at final compare; retain original intent, do not dispatch')
        try:
            _register(folder, plan['spec']['component'], d, owner, plan['original']['sddl'])
        except Exception as error:
            exclusive(operation/'registration-unknown.json', {'type': type(error).__name__,
                'state': 'unknown; inspect original operation, never repeat registration', 'at': time.time()})
            raise Held('Registration result unknown; inspect/reconcile this original operation') from error
        result = observe(plan)
        require(result['state'] == 'successor-definition', 'Registration readback differs; original operation held')
        require(result['instances'] == 0 and result['taskState'] in (1, 3), 'Unexpected activation held')
        exclusive(acceptance, {**result, 'registrationDispatched': True,
            'reconciled': False, 'originalIntentRetained': True})
        history_record(Path(plan['componentRoot']), operation, result)
        return {**result, 'registrationDispatched': True, 'reconciled': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('prepare', 'inspect', 'commit'))
    p.add_argument('--operation', type=Path, required=True)
    p.add_argument('--spec', type=Path)
    p.add_argument('--quiet-boundary', type=Path)
    p.add_argument('--quiet-sha256')
    a = p.parse_args()
    if a.command == 'prepare':
        result = prepare(json.loads(read(a.spec, private=True, limit=131072)), a.operation)
    elif a.command == 'inspect':
        result = inspect(a.operation)
    else:
        result = commit(a.operation, a.quiet_boundary, a.quiet_sha256)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
