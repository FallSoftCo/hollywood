"""Placement CAS/offline setup only, on fresh real PostgreSQL and HTTP.

Native/executor rows are explicitly synthetic. No models, native/controller
processes, production changes, or patch-byte application occur in this test.
"""
import asyncio
import json
import os
import secrets
from pathlib import Path

from integration_resource_routing import DirectoryFixture
from edit_plan_fixture import uid, sha
from hollywood_backbone.store import Store, digest, json_value


def migration_check():
    config = json.loads(Path(os.environ['CANONICAL_TEST_DATABASE']).read_text())
    store = Store(config)
    try:
        schema = Path(__file__).resolve().parents[1] / 'hollywood_backbone/schema.sql'
        old = schema.read_text().split('-- Existing enrollment remains intact.')[0]
        with store.pool.connection() as c:
            c.execute(old)
        credentials = {key: {'principalId': uid(), 'token': secrets.token_urlsafe(40)} for key in ('Owner', 'Global')}
        authority, host, room = uid(), uid(), uid()
        owner, agent = credentials['Owner']['principalId'], credentials['Global']['principalId']
        store.bootstrap(authority, 'SYNTHETIC schema28 enrollment migration', {'globalRoomId': room, 'credentials': credentials})
        store.placement(owner, host, 'SYNTHETIC migration host', 'synthetic-old-placement', {}, agent)
        with store.pool.connection() as c:
            before = c.execute('SELECT * FROM hb_placement WHERE agent=%s', (agent,)).fetchone()
        assert 'revision' not in before
        store.migrate()
        with store.pool.connection() as c:
            after = c.execute('SELECT * FROM hb_placement WHERE agent=%s', (agent,)).fetchone()
        assert after.pop('revision') == 1 and before == after
        return json_value({'schema28Placement': before, 'schema29PreservedFields': after, 'initialRevision': 1})
    finally:
        store.close()


async def main():
    migration = migration_check()
    f = DirectoryFixture()
    await f.start()
    owner, agent, host = f.credentials['Owner']['token'], f.agents['i'], f.hosts['host1']
    path = 'placements/' + agent['id']
    pending_path = 'connector/pending?hostId=' + host['id']
    pointer = f"agents/{agent['id']}/resource-defaults/{host['id']}"
    checks, races, native_trace = [], [], []

    async def inspect(token=owner, expected=200):
        return await f.http(token, 'GET', path, status=expected)

    async def change(enabled, status=200, payload=None, token=owner):
        before = await inspect()
        value = payload or {'operationId': uid(), 'revision': before['placement']['revision'], 'hostId': host['id'], 'enabled': enabled}
        return value, await f.http(token, 'PUT', path, value, status=status)

    async def select(workspace, revision=0, status=200):
        root = f.sql('SELECT revision FROM hb_physical_workspace WHERE id=%s', (workspace,))
        grant = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s', (workspace, agent['id']))
        value = {'operationId': uid(), 'revision': revision, 'state': 'selected', 'workspaceId': workspace,
                 'workspaceRevision': root['revision'], 'grantRevision': grant['revision']}
        return await f.http(owner, 'PUT', pointer, value, status=status)

    async def grant(workspace, state='active', status=200):
        root = f.sql('SELECT revision FROM hb_physical_workspace WHERE id=%s', (workspace,))
        prior = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s', (workspace, agent['id']))
        value = {'operationId': uid(), 'revision': prior['revision'] if prior else 0,
            'workspaceRevision': root['revision'], 'executionHost': host['id'], 'state': state, 'canRead': True, 'canWrite': True}
        return await f.http(owner, 'PUT', f"physical-workspaces/{workspace}/agent-grants/{agent['id']}", value, status=status)

    async def native_write(body, source, expected):
        async with f.client.put(f.url + path, json=body, headers={
                'Authorization': 'Bearer ' + f.agents['Global']['token'],
                'X-Hollywood-Native-Source': json.dumps(source)}) as response:
            result = await response.json()
            native_trace.append({'request': body, 'source': source, 'status': response.status,
                                 'result': result, 'syntheticNativeProvenance': True})
            assert response.status == expected, (response.status, result)
            return result

    try:
        initial = await inspect()
        assert initial['state'] == 'enabled' and initial['placement']['revision'] == 1
        assert not initial['newExecutionAdmitted'] and not initial['drainVerified'] and not initial['mayApply']
        assert await inspect(agent['token']) == initial
        await inspect(f.agents['outsider']['token'], 403)
        await inspect(host['token'], 403)
        await f.http(owner, 'GET', path + '?after=1', status=400)
        await f.http(owner, 'GET', 'placements/' + uid(), status=403)
        other_credentials = {key: {'principalId': uid(), 'token': secrets.token_urlsafe(40)} for key in ('Owner', 'Global')}
        f.store.bootstrap(uid(), 'SYNTHETIC placement isolation', {'globalRoomId': uid(), 'credentials': other_credentials})
        await inspect(other_credentials['Owner']['token'], 403)
        original = {'operationId': uid(), 'revision': 1, 'hostId': host['id'], 'enabled': False}
        for token in (agent['token'], host['token'], other_credentials['Owner']['token']):
            await f.http(token, 'PUT', path, original, status=403)
        for change_ in ({'revision': 0}, {'revision': True}, {'enabled': 'false'}, {'enabled': 0}, {'extra': 1}):
            await f.http(owner, 'PUT', path, {**original, **change_}, status=400)
        await f.http(owner, 'PUT', path, {key: value for key, value in original.items() if key != 'hostId'}, status=400)
        await f.http(owner, 'PUT', path, {**original, 'hostId': f.hosts['host2']['id']}, status=409)
        await f.http(owner, 'PUT', 'placements/' + uid(), original, status=403)
        checks.append('Existing schema28 rows migrate intact to revision1; strict inspect/CAS subject, authority and host scope')

        # Bind only synthetic current executor metadata; preserve one pending
        # request and every original row across the pause/resume operation.
        ordinary = f.ordinary(agent)
        queued = f.store.post(f.owner, f.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': agent['id'], 'body': 'SYNTHETIC queued request must stay pending'})
        protected_queries = ['SELECT * FROM hb_delivery ORDER BY id', 'SELECT * FROM hb_executor ORDER BY id',
            'SELECT * FROM hb_agent_context ORDER BY principal', 'SELECT * FROM hb_agent_execution ORDER BY delivery',
            'SELECT * FROM hb_event ORDER BY id', 'SELECT * FROM hb_agent_role ORDER BY principal',
            'SELECT * FROM hb_work ORDER BY id', 'SELECT * FROM hb_work_attempt ORDER BY delivery',
            'SELECT * FROM hb_claim ORDER BY agent,operation']
        protected = [f.sql(query, many=True) for query in protected_queries]
        paused = await f.http(owner, 'PUT', path, original)
        assert paused['state'] == 'paused' and paused['placement']['revision'] == 2 and not paused['drainVerified']
        assert await f.http(owner, 'PUT', path, original) == paused
        await f.http(owner, 'PUT', path, {**original, 'operationId': uid()}, status=409)
        await f.http(owner, 'PUT', path, {**original, 'enabled': True}, status=409)
        pending = await f.http(agent['token'], 'GET', pending_path)
        assert not pending['enabled'] and pending['busy']['id'] == ordinary and pending['pending']['id'] == queued['deliveries'][0]['id']
        assert [f.sql(query, many=True) for query in protected_queries] == protected
        await f.http(agent['token'], 'POST', 'connector/claim', {'operationId': uid(), 'hostId': host['id'], 'capabilities': []}, status=403)
        legacy = await f.http(owner, 'POST', 'placements', {'operationId': uid(), 'agentId': agent['id'],
            'hostId': host['id'], 'name': 'SYNTHETIC legacy registration',
            'identityFingerprint': 'synthetic-directory-proof', 'capabilities': {}})
        assert legacy == {'agentId': agent['id'], 'hostId': host['id'], 'enrolled': True}
        assert await inspect() == paused  # Legacy re-registration cannot resume.
        checks.append('Pause denies new claims while preserving live executor, queue, role/context, requests and original operation; legacy registration does not resume')

        workspace = uid()
        await f.register_root(workspace, host['id'], f.out / 'resource' / workspace)
        assert (await grant(workspace))['grant']['state'] == 'active'
        setup = await select(workspace)
        assert setup['effectiveState'] == 'held' and setup['holdReason'] == 'participation-unavailable'
        assert setup['resource'] is None and not setup['mayApply']
        own = await f.http(agent['token'], 'GET', f"agents/{agent['id']}/resources?executionHost={host['id']}")
        assert own['resources'] == [] and own['default']['effectiveState'] == 'held'
        admin = await f.http(owner, 'GET', f"agents/{agent['id']}/resources?executionHost={host['id']}")
        assert len(admin['resources']) == 1

        actual = f.resources[workspace]['root'] / 'ordinary.txt'
        actual.write_bytes(b'before\n')
        file = {'relativePath': 'ordinary.txt', 'beforeSha256': sha(b'before\n'), 'beforeSize': 7,
                'afterSha256': sha(b'after\n'), 'afterSize': 6}
        source = {**agent['source'], 'tool': 'apply_patch', 'callId': uid()}
        permit_id, patch_sha = uid(), sha(b'SYNTHETIC patch identity only')
        def patch(action='preflight', permit_revision=None, outcome=None):
            command = {'action': action, 'permitId': permit_id, 'briefingSequence': 0,
                'note': 'SYNTHETIC placement fencing only', 'sourceRevision': None, 'patchSha256': patch_sha,
                'filesObserved': [{'relativePath': file['relativePath'], 'sha256': file['beforeSha256'], 'size': file['beforeSize']}]}
            if action == 'preflight':
                command.update(files=[file], leaseSeconds=30)
            else:
                command['permitRevision'] = permit_revision
            if outcome:
                command['outcome'] = outcome
            value = {'schema': 'canonical-delivery-edits/v1', 'scope': 'delivery', 'operationId': uid(),
                'bridgeRevision': 1, 'source': source, 'executorId': agent['executor'], 'descriptorDigest': 'd'*64,
                'serviceBindingDigest': 'e'*64, 'channel': 'captured-app-server-rpc/v1', 'command': command}
            root = f.resources[workspace]
            access = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s', (workspace, agent['id']))
            value['target'] = {'schema': 'hollywood-physical-target/v1', 'workspaceId': workspace,
                'workspaceRevision': root['revision'], 'grantRevision': access['revision'], 'environmentSha256': root['environment'],
                'rootIdentitySha256': root['identity'], 'resolution': 'beneath-no-symlinks/v1', 'actionDigest': digest(value),
                'paths': [{'relativePath': 'ordinary.txt', 'resolvedPath': str(actual)}]}
            return value
        preflight = patch()
        await f.http(host['token'], 'POST', 'collaboration/delivery-edits', preflight, status=409)
        assert not f.sql('SELECT 1 FROM hb_edit_permit')
        resume_body, resumed = await change(True)
        assert resumed['placement']['revision'] == 3 and resumed['state'] == 'enabled'
        assert (await f.http(owner, 'GET', pointer))['effectiveState'] == 'selected'
        assert f.sql('SELECT state FROM hb_delivery WHERE id=%s', (queued['deliveries'][0]['id'],))['state'] == 'pending'
        # Enabled placement opens this prerequisite, but capability/admission
        # checks continue to reject a deliberately obsolete connector.
        await f.http(agent['token'], 'POST', 'connector/claim', {'operationId': uid(), 'hostId': host['id'], 'capabilities': []}, status=409)
        await grant(workspace, state='revoked')
        await f.http(host['token'], 'POST', 'collaboration/delivery-edits', patch(), status=403)
        await grant(workspace)
        accepted = await f.http(host['token'], 'POST', 'collaboration/delivery-edits', patch())
        permit = accepted['receipt']['permit']
        assert permit['state'] == 'reserved' and not accepted['admission']
        checks.append('Paused administrator grant/default setup is held; resume changes no request; enabled preflight still rejects revoked access and requires live source')

        _, paused_again = await change(False)
        locks_before = f.sql('SELECT * FROM hb_edit_file_lock ORDER BY relative_path', many=True)
        permit_before = f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (permit_id,))
        await f.http(host['token'], 'POST', 'collaboration/delivery-edits', patch('begin', permit['revision']), status=409)
        held_read = await f.http(host['token'], 'GET', 'collaboration/delivery-edits/' + permit_id)
        assert held_read['effectiveState'] == 'held' and held_read['permit']['state'] == 'reserved'
        assert f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (permit_id,)) == permit_before
        assert f.sql('SELECT * FROM hb_edit_file_lock ORDER BY relative_path', many=True) == locks_before
        assert (await f.http(owner, 'GET', pointer))['holdReason'] == 'participation-invalidated'
        await change(True)
        assert (await f.http(owner, 'GET', pointer))['effectiveState'] == 'held'
        begin = await f.http(host['token'], 'POST', 'collaboration/delivery-edits', patch('begin', permit['revision']))
        assert begin['admission']['mayApply'] and begin['admission']['oneShotOriginalResponseOnly']
        executing = begin['receipt']['permit']
        settled = await f.http(host['token'], 'POST', 'collaboration/delivery-edits', patch('settle', executing['revision'], 'unknown'))
        assert settled['receipt']['permit']['state'] == 'held'
        unknown = f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (permit_id,))
        await change(False)
        held_selected = await select(workspace, setup['selectionRevision'])
        assert held_selected['holdReason'] == 'participation-unavailable'
        await change(True)
        assert (await f.http(owner, 'GET', pointer))['effectiveState'] == 'selected'
        assert f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (permit_id,)) == unknown
        assert f.sql('SELECT * FROM hb_edit_file_lock ORDER BY relative_path', many=True) == locks_before
        assert actual.read_bytes() == b'before\n'  # No patch bytes applied.
        checks.append('Pause fences reserved begin without release; explicit resume does not revive invalidated default; paused reselection prepares new routing; unknown permits/locks persist')

        # Same revision, distinct operations: exactly one transition commits.
        race_revision = (await inspect())['placement']['revision']
        async def contender(enabled):
            body = {'operationId': uid(), 'revision': race_revision, 'hostId': host['id'], 'enabled': enabled}
            async with f.client.put(f.url + path, json=body, headers={'Authorization': 'Bearer ' + owner}) as response:
                value = {'request': body, 'status': response.status, 'result': await response.json()}
                races.append(value)
                return value
        await asyncio.gather(contender(False), contender(True))
        assert sorted(row['status'] for row in races) == [200, 409]
        assert (await inspect())['placement']['revision'] == race_revision + 1
        if (await inspect())['state'] == 'paused':
            await change(True)
        # Old original pause receipt remains immutable, with no new state effect.
        current = await inspect()
        assert await f.http(owner, 'PUT', path, original) == paused
        assert await inspect() == current
        checks.append('Concurrent CAS accepts one successor; same-state operations remain revisioned; original historical receipt never re-pauses or resumes')

        # Live synthetic administrator source remains mandatory for writes. A
        # paused administrator cannot self-resume through native provenance.
        global_agent = f.agents['Global']
        global_delivery = f.ordinary(global_agent)
        body = {'operationId': uid(), 'revision': current['placement']['revision'], 'hostId': host['id'], 'enabled': False}
        native_source = {**global_agent['source'], 'tool': 'hollywood_placement_state', 'argumentsDigest': digest(body)}
        await f.http(global_agent['token'], 'PUT', path, body, status=403)
        native_accepted = await native_write(body, native_source, 200)
        assert native_accepted['state'] == 'paused'
        global_path = 'placements/' + global_agent['id']
        global_before = await f.http(owner, 'GET', global_path)
        await f.http(owner, 'PUT', global_path, {'operationId': uid(), 'revision': global_before['placement']['revision'],
            'hostId': host['id'], 'enabled': False})
        await native_write(body, native_source, 409)  # Replay cannot bypass a paused source.
        paused_admin = await f.http(global_agent['token'], 'GET', global_path)
        assert paused_admin['state'] == 'paused'
        async with f.client.put(f.url + global_path, json={'operationId': uid(),
            'revision': paused_admin['placement']['revision'], 'hostId': host['id'], 'enabled': True}, headers={
                'Authorization': 'Bearer ' + global_agent['token'], 'X-Hollywood-Native-Source': json.dumps(native_source)}) as response:
            assert response.status == 409
            native_trace.append({'selfResumeStatus': response.status, 'result': await response.json()})
        # Original own administrative receipt remains readable while paused.
        receipt = await f.http(global_agent['token'], 'GET', 'administration/commands/' + body['operationId'])
        assert receipt['acceptedResult'] == native_accepted
        await f.http(owner, 'PUT', global_path, {'operationId': uid(), 'revision': paused_admin['placement']['revision'],
            'hostId': host['id'], 'enabled': True})
        f.sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (global_delivery,))
        await native_write(body, native_source, 409)
        checks.append('Current source-v2 administrator and enabled placement required even for replay/self-resume; original receipt remains inspectable without fake live claim')

        # GET waits behind the sanctioned writer's authority fence and sees its
        # final state. No direct row patch is used for pause/resume in this test.
        await change(True)
        with f.store.pool.connection() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('native-authority:' + f.ids['authority'],))
            waiting = asyncio.create_task(inspect())
            await asyncio.sleep(0.05)
            assert not waiting.done()
        assert (await waiting)['state'] == 'enabled'
        capabilities = await f.http(owner, 'GET', 'hosts/' + host['id'] + '/collaboration-bridge')
        assert capabilities['placementControl'] == 'canonical-placement-control/v1' and capabilities['pausedResourceSetup']
        assert capabilities['deliveryEditSchema'] == 'canonical-delivery-edits/v1'
        history = f.sql('SELECT * FROM hb_placement_state_revision WHERE agent=%s ORDER BY revision', (agent['id'],), many=True)
        current = await inspect()
        assert [row['revision'] for row in history] == list(range(2, current['placement']['revision'] + 1))
        assert all(row['after_state']['revision'] == row['before_state']['revision'] + 1 for row in history)
        assert current['placement']['host'] == initial['placement']['host'] and current['placement']['generation'] == initial['placement']['generation']
        assert f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (permit_id,)) == unknown
        assert f.sql('SELECT * FROM hb_edit_file_lock ORDER BY relative_path', many=True) == locks_before
        assert f.sql('SELECT state FROM hb_delivery WHERE id=%s', (queued['deliveries'][0]['id'],))['state'] == 'pending'
        checks.append('Shared inspection fence, additive capability, contiguous append-only state revisions and unchanged unknown ownership/queue')

        evidence = {'migration': migration, 'initial': initial, 'originalPause': paused, 'current': current,
            'stateHistory': history, 'pending': await f.http(agent['token'], 'GET', pending_path),
            'unknownPermit': unknown, 'preservedLocks': locks_before, 'nativeHTTP': native_trace,
            'originalNativeReceipt': receipt, 'finalCapabilities': capabilities}
        (f.out / 'placement-control-evidence.json').write_text(json.dumps(json_value(evidence), indent=2) + '\n')
        (f.out / 'placement-cas-race.json').write_text(json.dumps(races, indent=2) + '\n')
        (f.out / 'native-source-http.json').write_text(json.dumps(native_trace, indent=2) + '\n')
        result = {'state': 'passed', 'checks': checks, 'httpCalls': len(f.trace) + len(native_trace) + len(races),
            'placementRevision': current['placement']['revision'], 'nativeProvenance': 'SYNTHETIC fixture only',
            'nativeStarts': 0, 'controllerStarts': 0, 'modelStarts': 0, 'patchBytesApplied': 0,
            'productionChanges': 0, 'independentDrainStillRequired': True, 'nativeRuntimeProof': False}
        (f.out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'state': 'passed', 'groups': len(checks), 'httpCalls': result['httpCalls']}))
    finally:
        await f.close()


if __name__ == '__main__':
    asyncio.run(main())
