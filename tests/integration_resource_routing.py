"""New directory/default mechanisms only, on fresh real PostgreSQL and HTTP.

Native source/executor rows are explicitly SYNTHETIC. No model, native runtime,
worker or protected controller is started, and no patch or plan is executed.
"""
import asyncio
import json
import os
import secrets
from pathlib import Path

from aiohttp import ClientSession, web

from edit_plan_fixture import Fixture, uid
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest, json_value


class DirectoryFixture(Fixture):
    def __init__(self):
        self.out = Path(os.environ['CANONICAL_TEST_OUTPUT'])
        config = json.loads(Path(os.environ['CANONICAL_TEST_DATABASE']).read_text())
        assert str(self.out).startswith('/dev/shm/') and config['host'].startswith('/dev/shm/')
        self.store = Store(config)
        self.store.migrate()
        self.store.migrate()  # Additive migration is idempotent.
        self.ids = {key: uid() for key in ('authority', 'room', 'host1', 'host2')}
        self.credentials = {key: {'principalId': uid(), 'token': secrets.token_urlsafe(40)} for key in ('Owner', 'Global')}
        self.owner = self.credentials['Owner']['principalId']
        self.store.bootstrap(self.ids['authority'], 'SYNTHETIC resource routing only', {
            'globalRoomId': self.ids['room'], 'credentials': self.credentials})
        self.hosts, self.agents, self.resources, self.trace = {}, {}, {}, []
        for key in ('host1', 'host2'):
            host = {'id': self.ids[key], 'controller': uid(), 'token': secrets.token_urlsafe(40), 'revision': 1}
            self.store.principal(self.owner, host['controller'], 'SYNTHETIC directory controller ' + key, 'service', host['token'])
            self.hosts[key] = host
        for key in ('i', 'outsider', 'remote', 'Global'):
            host = self.hosts['host2' if key == 'remote' else 'host1']
            credential = self.credentials.get(key)
            agent = {'id': credential['principalId'] if credential else uid(),
                     'token': credential['token'] if credential else secrets.token_urlsafe(40),
                     'host': host, 'privateWorkspace': uid(), 'name': key}
            if not credential:
                self.store.principal(self.owner, agent['id'], 'SYNTHETIC directory agent ' + key, 'agent', agent['token'])
            self.store.membership(self.owner, self.ids['room'], agent['id'], {'can_read': True, 'can_post': True, 'can_request': True}, 0)
            self.store.placement(self.owner, host['id'], 'SYNTHETIC ' + host['id'], 'synthetic-directory-proof', {}, agent['id'])
            if not self.sql('SELECT 1 FROM hb_host_admission WHERE host=%s', (host['id'],)):
                self.sql('''INSERT INTO hb_host_admission(host,authority,controller,machine_identity,
                    max_concurrent,reserved_interactive,memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
                    VALUES(%s,%s,%s,'synthetic',16,0,1,0,300,300)''', (host['id'], self.ids['authority'], host['controller']))
            self.agents[key] = agent

    async def start(self):
        self.runner = web.AppRunner(application(self.store))
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v3/'
        self.client = ClientSession()
        for host in self.hosts.values():
            await self.http(self.credentials['Owner']['token'], 'PUT', f"hosts/{host['id']}/collaboration-bridge", {
                'operationId': uid(), 'revision': 0, 'controllerId': host['controller'], 'state': 'active',
                'protocol': 'canonical-edit-plans/v2', 'adapterSha256': 'a' * 64, 'enrollmentSha256': 'b' * 64})

    def ordinary(self, agent):
        request = self.store.post(self.owner, self.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': agent['id'], 'body': 'SYNTHETIC resource metadata request only'})
        self.bind(agent, request['deliveries'][0]['id'], request['event']['id'])
        return request['deliveries'][0]['id']


async def main():
    f = DirectoryFixture()
    await f.start()
    owner, agent, host = f.credentials['Owner']['token'], f.agents['i'], f.hosts['host1']
    base = f"agents/{agent['id']}"
    listing = base + '/resources?executionHost=' + host['id']
    pointer = base + '/resource-defaults/' + host['id']
    checks = []

    async def access(workspace, state='active', read=True, write=True, execution_host=None):
        root = f.sql('SELECT revision FROM hb_physical_workspace WHERE id=%s', (workspace,))
        old = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s', (workspace, agent['id']))
        return await f.http(owner, 'PUT', f"physical-workspaces/{workspace}/agent-grants/{agent['id']}", {
            'operationId': uid(), 'revision': old['revision'] if old else 0, 'workspaceRevision': root['revision'],
            'executionHost': execution_host or host['id'], 'state': state, 'canRead': read, 'canWrite': write})

    def selection(workspace, revision=0):
        root = f.sql('SELECT revision FROM hb_physical_workspace WHERE id=%s', (workspace,))
        grant = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s', (workspace, agent['id']))
        return {'operationId': uid(), 'revision': revision, 'state': 'selected', 'workspaceId': workspace,
                'workspaceRevision': root['revision'], 'grantRevision': grant['revision']}

    async def view():
        return await f.http(owner, 'GET', pointer)

    async def reselect(workspace):
        body = selection(workspace, (await view())['selectionRevision'])
        return await f.http(owner, 'PUT', pointer, body)

    async def root_state(workspace, state):
        row = f.sql('SELECT * FROM hb_physical_workspace WHERE id=%s', (workspace,))
        return await f.http(owner, 'PUT', f"hosts/{row['host']}/physical-workspaces/{workspace}", {
            'operationId': uid(), 'revision': row['revision'], 'state': state, 'rootPath': row['root_path'],
            'environmentSha256': row['environment_sha256'], 'rootIdentitySha256': row['root_identity_sha256'],
            'registrationSha256': row['registration_sha256']})

    try:
        assert f.sql('SELECT max(version) AS version FROM hb_schema')['version'] == 28
        assert (await view())['effectiveState'] == 'missing'
        assert (await f.http(agent['token'], 'GET', pointer))['selectionRevision'] == 0
        assert not f.sql('SELECT 1 FROM hb_work') and not f.sql('SELECT 1 FROM hb_agent_context')
        # Stable sorted UUIDs guarantee hidden records precede readable records.
        workspaces = sorted(uid() for _ in range(11))
        for workspace in workspaces:
            await f.register_root(workspace, host['id'], f.out / 'resources' / workspace)
        for workspace in workspaces[:9]:
            await access(workspace)
        await access(workspaces[0], state='revoked')
        await root_state(workspaces[1], 'revoked')
        await access(workspaces[2], read=False, write=False)
        await access(workspaces[3], write=False)
        visible = workspaces[3:9]
        first = await f.http(agent['token'], 'GET', listing + '&limit=2')
        assert [row['workspace']['id'] for row in first['resources']] == visible[:2]
        assert first['nextCursor'] == visible[1] and not first['resources'][0]['grant']['can_write']
        found, cursor = [], None
        while True:
            page = await f.http(agent['token'], 'GET', listing + '&limit=2' + ('&after=' + cursor if cursor else ''))
            found += [row['workspace']['id'] for row in page['resources']]
            cursor = page['nextCursor']
            if cursor is None:
                break
        assert found == visible
        admin = await f.http(owner, 'GET', listing + '&limit=8')
        assert len(admin['resources']) == 8 and admin['resources'][0]['grant']['state'] == 'revoked'
        roots, cursor = [], None
        while True:
            page = await f.http(owner, 'GET', 'physical-workspaces?limit=4' + ('&after=' + cursor if cursor else ''))
            roots += [row['id'] for row in page['workspaces']]
            cursor = page['nextCursor']
            if cursor is None:
                break
        assert roots == workspaces
        assert (await f.http(owner, 'GET', 'physical-workspaces?hostId=' + f.hosts['host2']['id']))['workspaces'] == []
        for token in (agent['token'], host['token']):
            await f.http(token, 'GET', 'physical-workspaces', status=403)
        for token in (f.agents['outsider']['token'], host['token']):
            await f.http(token, 'GET', listing, status=403)
            await f.http(token, 'GET', pointer, status=403)
        for query in ('', '?limit=9', '?executionHost=bad', '?executionHost=' + host['id'] + '&limit=0',
                      '?executionHost=' + host['id'] + '&limit=2&limit=3', '?executionHost=' + host['id'] + '&unknown=1'):
            await f.http(owner, 'GET', base + '/resources' + query, status=400)
        await f.http(owner, 'GET', pointer + '?after=' + uid(), status=400)
        await f.http(owner, 'GET', f'agents/{uid()}/resources?executionHost=' + host['id'], status=403)
        other_authority, other_room = uid(), uid()
        other_credentials = {key: {'principalId': uid(), 'token': secrets.token_urlsafe(40)} for key in ('Owner', 'Global')}
        f.store.bootstrap(other_authority, 'SYNTHETIC isolated directory', {
            'globalRoomId': other_room, 'credentials': other_credentials})
        other_owner = other_credentials['Owner']['token']
        assert (await f.http(other_owner, 'GET', 'physical-workspaces'))['workspaces'] == []
        await f.http(other_owner, 'GET', listing, status=403)
        await f.http(other_owner, 'GET', pointer, status=403)
        await f.http(other_owner, 'PUT', pointer, selection(workspaces[4]), status=403)
        checks.append('Bounded admin and owner discovery; visibility before keyset pagination; query and actor scope')

        target = workspaces[4]
        before_grants = f.sql('SELECT * FROM hb_physical_agent_grant ORDER BY workspace,principal', many=True)
        before_bridge = f.sql('SELECT * FROM hb_collaboration_bridge ORDER BY host', many=True)
        chosen = selection(target)
        accepted = await f.http(owner, 'PUT', pointer, chosen)
        assert accepted['effectiveState'] == 'selected' and accepted['selectionRevision'] == 1 and not accepted['mayApply']
        assert await f.http(owner, 'PUT', pointer, chosen) == accepted
        await f.http(owner, 'PUT', pointer, {**chosen, 'operationId': uid()}, status=409)
        await f.http(owner, 'PUT', pointer, {**chosen, 'state': 'cleared', 'workspaceId': None,
                                          'workspaceRevision': None, 'grantRevision': None}, status=409)
        await f.http(agent['token'], 'PUT', pointer, selection(target, 1), status=403)
        await f.http(host['token'], 'PUT', pointer, selection(target, 1), status=403)
        for change in ({'workspaceId': None}, {'workspaceRevision': None}, {'grantRevision': 999},
                       {'revision': True}, {'extra': 1}, {'state': 'cleared'}):
            status = 409 if change == {'grantRevision': 999} else 400
            await f.http(owner, 'PUT', pointer, {**selection(target, 1), **change}, status=status)
        await f.http(owner, 'PUT', pointer, {k: v for k, v in selection(target, 1).items() if k != 'workspaceRevision'}, status=400)
        await f.http(owner, 'PUT', pointer, selection(workspaces[3], 1), status=403)
        assert f.sql('SELECT * FROM hb_physical_agent_grant ORDER BY workspace,principal', many=True) == before_grants
        assert f.sql('SELECT * FROM hb_collaboration_bridge ORDER BY host', many=True) == before_bridge
        assert not f.sql('SELECT 1 FROM hb_edit_file_lock') and not f.sql('SELECT 1 FROM hb_edit_permit')
        assert not f.sql('SELECT 1 FROM hb_work') and not f.sql('SELECT 1 FROM hb_agent_context')
        checks.append('Separate default CAS, strict DTO, exact revisions and original receipt replay; no grant/admission/context changes')

        await access(target, state='revoked')
        assert (await view())['holdReason'] == 'access-unavailable'
        assert await f.http(owner, 'PUT', pointer, chosen) == accepted  # Historical result only.
        assert (await view())['resource'] is None
        await access(target)
        assert (await view())['holdReason'] == 'selection-revisions-changed'
        await reselect(target)
        await root_state(target, 'revoked')
        assert (await view())['effectiveState'] == 'held'
        await root_state(target, 'active')
        assert (await view())['holdReason'] == 'selection-revisions-changed'
        await reselect(target)
        f.sql('UPDATE hb_placement SET enabled=false WHERE agent=%s', (agent['id'],))
        assert (await view())['holdReason'] == 'participation-invalidated'
        assert (await f.http(agent['token'], 'GET', listing))['resources'] == []
        f.sql('UPDATE hb_placement SET enabled=true WHERE agent=%s', (agent['id'],))
        assert (await view())['effectiveState'] == 'held'
        await reselect(target)
        f.sql('UPDATE hb_principal SET active=false WHERE id=%s', (agent['id'],))
        assert (await view())['holdReason'] == 'participation-invalidated'
        await f.http(agent['token'], 'GET', pointer, status=403)
        f.sql('UPDATE hb_principal SET active=true WHERE id=%s', (agent['id'],))
        assert (await view())['effectiveState'] == 'held'
        await reselect(target)
        # An agent grant on this execution host is not proof of a compatible
        # remote filesystem. V1 defaults explicitly require a local root.
        remote_root = uid()
        await f.register_root(remote_root, f.hosts['host2']['id'], f.out / 'remote' / remote_root)
        await access(remote_root)
        await f.http(owner, 'PUT', pointer, selection(remote_root, (await view())['selectionRevision']), status=403)
        checks.append('Revocation/regrant/registration drift hold pinned selections; participation invalidation never silently revives; local-only default')

        clear = {'operationId': uid(), 'revision': (await view())['selectionRevision'], 'state': 'cleared',
                 'workspaceId': None, 'workspaceRevision': None, 'grantRevision': None}
        cleared = await f.http(owner, 'PUT', pointer, clear)
        assert cleared['effectiveState'] == 'cleared' and cleared['resource'] is None
        # Two independent operations contend for the same absent successor rev.
        race_body = selection(target, cleared['selectionRevision'])
        async def contender():
            async with f.client.put(f.url + pointer, json={**race_body, 'operationId': uid()},
                                    headers={'Authorization': 'Bearer ' + owner}) as response:
                return {'status': response.status, 'result': await response.json()}
        race = await asyncio.gather(contender(), contender())
        assert sorted(row['status'] for row in race) == [200, 409]
        (f.out / 'default-cas-race.json').write_text(json.dumps(race, indent=2) + '\n')
        checks.append('Explicit clear and simultaneous selection CAS; one accepted successor only')

        # Source-v2 administrator path uses the same current native-source and
        # attached executor validation as existing administrative writes.
        global_agent = f.agents['Global']
        f.ordinary(global_agent)
        native_body = selection(target, (await view())['selectionRevision'])
        source = {**global_agent['source'], 'tool': 'hollywood_resource_default', 'argumentsDigest': digest(native_body)}
        await f.http(global_agent['token'], 'PUT', pointer, native_body, status=403)
        async def native_set(value, status):
            async with f.client.put(f.url + pointer, json=value, headers={
                'Authorization': 'Bearer ' + global_agent['token'], 'X-Hollywood-Native-Source': json.dumps(source)}) as response:
                result = await response.json()
                assert response.status == status, (response.status, result)
                row = {'method': 'PUT', 'path': pointer, 'status': response.status, 'request': value,
                       'source': source, 'result': result, 'syntheticNativeProvenance': True}
                with (f.out / 'native-source-http.jsonl').open('a') as out:
                    out.write(json.dumps(row) + '\n')
                return result
        assert (await native_set(native_body, 200))['effectiveState'] == 'selected'
        f.sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (source['deliveryId'],))
        await native_set(native_body, 409)
        assert f.sql('SELECT 1 FROM hb_native_effect WHERE operation=%s', (native_body['operationId'],))
        checks.append('Agent administrator requires current source-v2 provenance even for historical operation replay')

        ordinary = f.ordinary(agent)
        context_before = f.sql('SELECT * FROM hb_agent_context WHERE principal=%s', (agent['id'],))
        ordinary_result = await f.http(host['token'], 'GET', 'collaboration/delivery-resource-default/' + ordinary)
        assert ordinary_result['agentId'] == agent['id'] and ordinary_result['default']['effectiveState'] == 'selected'
        await f.http(f.hosts['host2']['token'], 'GET', 'collaboration/delivery-resource-default/' + ordinary, status=403)
        await f.http(agent['token'], 'GET', 'collaboration/delivery-resource-default/' + ordinary, status=403)
        source_event = f.store.post(f.owner, f.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': global_agent['id'], 'body': 'SYNTHETIC structured wrapper metadata only'})
        goal_id, work_id = uid(), uid()
        goal = f.store.workflow(f.owner, 'goal.create', f.ids['room'], uid(), {'id': goal_id,
            'sourceEventId': source_event['event']['id'], 'title': 'SYNTHETIC routing', 'objective': 'Metadata only',
            'acceptance': 'No mutation admitted', 'reviewBudget': 0})
        f.store.workflow(f.owner, 'goal.cooperation', goal_id, uid(), {'revision': goal['goalRevision'],
            'cooperationRevision': 0, 'policy': 'leader_award', 'executorIds': [agent['id']], 'reviewerId': None})
        f.store.workflow(f.owner, 'goal.work', goal_id, uid(), {'id': work_id,
            'revision': f.sql('SELECT revision FROM hb_goal WHERE id=%s', (goal_id,))['revision'],
            'roomId': f.ids['room'], 'ownerId': agent['id'], 'specification': 'SYNTHETIC wrapper',
            'acceptance': 'Only default metadata read', 'dependencies': []})
        assigned = f.store.workflow(f.owner, 'work.assign', work_id, uid(), {'revision': 1})
        f.bind(agent, assigned['deliveryId'], assigned['requestEventId'])
        structured = await f.http(host['token'], 'GET', 'collaboration/delivery-resource-default/' + assigned['deliveryId'])
        assert structured['default'] == ordinary_result['default']
        assert f.sql('SELECT * FROM hb_agent_context WHERE principal=%s', (agent['id'],)) == context_before
        assert not f.sql('SELECT 1 FROM hb_physical_grant') and not f.sql('SELECT 1 FROM hb_edit_permit')
        assert not f.sql('SELECT 1 FROM hb_path_claim') and not f.sql('SELECT 1 FROM hb_edit_file_lock')
        await f.http(other_owner, 'GET', 'collaboration/delivery-resource-default/' + ordinary, status=403)
        checks.append('Controller derives ordinary and structured default from delivery; planned grants, context and locks remain separate')

        # A GET waiting behind an exclusive authority fence must observe the
        # committed revocation rather than a pre-wait authorization snapshot.
        with f.store.pool.connection() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('native-authority:' + f.ids['authority'],))
            waiting = asyncio.create_task(f.http(agent['token'], 'GET', listing))
            await asyncio.sleep(0.05)
            assert not waiting.done()
            connection.execute("UPDATE hb_physical_agent_grant SET state='revoked',revision=revision+1 WHERE workspace=%s AND principal=%s", (target, agent['id']))
        page = await waiting
        assert target not in [row['workspace']['id'] for row in page['resources']]
        assert page['default']['effectiveState'] == 'held'
        # Recheck credentials after waiting, then reauthenticate the next page.
        with f.store.pool.connection() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('native-authority:' + f.ids['authority'],))
            waiting = asyncio.create_task(f.http(agent['token'], 'GET', pointer, status=403))
            await asyncio.sleep(0.05)
            assert not waiting.done()
            connection.execute('UPDATE hb_token SET revoked=true WHERE principal=%s', (agent['id'],))
        await waiting
        await f.http(agent['token'], 'GET', listing, status=403)
        original_path = f.sql('SELECT root_path FROM hb_physical_workspace WHERE id=%s', (target,))['root_path']
        f.sql('UPDATE hb_physical_workspace SET root_path=%s WHERE id=%s', ('/' + 'x' * 40000, target))
        await f.http(owner, 'GET', 'physical-workspaces?limit=8', status=409)
        f.sql('UPDATE hb_physical_workspace SET root_path=%s WHERE id=%s', (original_path, target))
        capabilities = await f.http(owner, 'GET', 'hosts/' + host['id'] + '/collaboration-bridge')
        assert capabilities['resourceRouting'] == 'canonical-agent-resources/v1' and capabilities['resourceDefaults'] and capabilities['resourceMetadata']
        assert capabilities['deliveryEditSchema'] == 'canonical-delivery-edits/v1'
        checks.append('Shared read fence observes revocation; next page reauthenticates; response byte cap fails without truncation; additive capability')

        # A synthetic existing permit/physical lock lets this narrow scenario
        # prove routing writes preserve rows, without rerunning permit admission.
        await access(target)
        await reselect(target)
        permit_id = uid()
        f.sql('''INSERT INTO hb_edit_permit(id,authority,state,expires_at,source,bridge,bridge_revision,
            proof,consent_digest,scope_kind,delivery,room) VALUES(%s,%s,'executing',
            clock_timestamp()+interval '5 minutes',%s::jsonb,%s,1,'{}','synthetic-only','delivery',%s,%s)''',
            (permit_id, f.ids['authority'], json.dumps(agent['source']), host['controller'], ordinary, f.ids['room']))
        f.sql('INSERT INTO hb_edit_file_lock(authority,host,workspace_id,relative_path,permit) VALUES(%s,%s,%s,%s,%s)',
              (f.ids['authority'], host['id'], target, 'synthetic-existing-lock.txt', permit_id))
        protected_queries = ['SELECT * FROM hb_edit_permit ORDER BY id',
            'SELECT * FROM hb_edit_file_lock ORDER BY relative_path', 'SELECT * FROM hb_work ORDER BY id',
            'SELECT * FROM hb_work_attempt ORDER BY delivery', 'SELECT * FROM hb_agent_context ORDER BY principal',
            'SELECT * FROM hb_physical_agent_grant ORDER BY workspace,principal',
            'SELECT * FROM hb_collaboration_bridge ORDER BY host']
        protected_before = [f.sql(query, many=True) for query in protected_queries]
        await reselect(target)
        await f.http(owner, 'PUT', pointer, {**clear, 'operationId': uid(), 'revision': (await view())['selectionRevision']})
        assert [f.sql(query, many=True) for query in protected_queries] == protected_before
        checks.append('Select/clear preserve an existing physical lock, permit, grants, bridge, contexts and structured work/attempts')

        revisions = f.sql('SELECT * FROM hb_resource_default_revision ORDER BY principal,execution_host,revision', many=True)
        current = await view()
        assert len(revisions) == current['selectionRevision']
        assert [row['revision'] for row in revisions] == list(range(1, len(revisions) + 1))
        assert len(f.sql('SELECT * FROM hb_resource_default_hold', many=True)) == 2
        (f.out / 'resource-default-evidence.json').write_text(json.dumps(json_value({
            'current': current, 'history': revisions, 'holds': f.sql('SELECT * FROM hb_resource_default_hold', many=True),
            'originalReceipt': f.sql('SELECT * FROM hb_administrative_command WHERE operation=%s', (chosen['operationId'],)),
            'nativeEffects': f.sql('SELECT * FROM hb_native_effect', many=True)}), indent=2) + '\n')
        (f.out / 'qualification.json').write_text(json.dumps({'state': 'passed', 'checks': checks,
            'httpCalls': len(f.trace) + len(race) + 2, 'selectionRevisions': len(revisions),
            'nativeStarts': 0, 'controllerStarts': 0, 'modelStarts': 0, 'productionChanges': 0,
            'nativeProvenance': 'SYNTHETIC fixture only', 'nativeRuntimeProof': False}, indent=2) + '\n')
        print(json.dumps({'state': 'passed', 'groups': len(checks), 'httpCalls': len(f.trace) + len(race) + 2}))
    finally:
        await f.close()


if __name__ == '__main__':
    asyncio.run(main())
