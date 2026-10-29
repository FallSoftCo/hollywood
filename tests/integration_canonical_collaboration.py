"""Fresh actual PostgreSQL/HTTP path-claim qualification.

All native lifecycle, controller and bridge facts are explicitly SYNTHETIC input
rows. No native process or protected bridge is started or qualified here.
"""
import asyncio
import copy
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from psycopg.types.json import Jsonb

from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest


def identity():
    return str(uuid4())


async def main():
    import hollywood_backbone
    source = Path(__file__).resolve().parents[1]
    assert Path(hollywood_backbone.__file__).resolve().is_relative_to(source)
    out = Path(os.environ['CANONICAL_TEST_OUTPUT'])
    config = json.loads(Path(os.environ['CANONICAL_TEST_DATABASE']).read_text())
    # New qualification uses only an explicit disposable schema on disk.
    namespace = os.environ['CANONICAL_TEST_SCHEMA']
    assert namespace.startswith('lax_claim_inspection_') and namespace.isidentifier()
    assert config.get('options', '').split()[-2:] == ['-c', 'search_path=' + namespace]
    assert out.is_absolute() and out.exists()
    filesystem = subprocess.check_output(['findmnt','-n','-o','FSTYPE','-T',str(out)],text=True).strip()
    assert filesystem not in ('tmpfs','ramfs'), 'Disk-backed qualification output required'
    store = Store(config)
    store.migrate()
    ids = {name: identity() for name in ('authority', 'room', 'goal', 'host1', 'host2', 'workspace1', 'workspace2')}
    credentials = {name: {'principalId': identity(), 'token': secrets.token_urlsafe(40)} for name in ('Owner', 'Global')}
    owner = credentials['Owner']['principalId']
    store.bootstrap(ids['authority'], 'SYNTHETIC canonical collaboration qualification', {
        'globalRoomId': ids['room'], 'credentials': credentials})

    def sql(query, values=(), many=False):
        with store.pool.connection() as connection:
            cursor = connection.execute(query, values)
            if cursor.description:
                return cursor.fetchall() if many else cursor.fetchone()

    hosts = {}
    for name in ('host1', 'host2'):
        controller, token = identity(), secrets.token_urlsafe(40)
        store.principal(owner, controller, 'SYNTHETIC controller ' + name, 'service', token)
        hosts[name] = {'id': ids[name], 'controller': controller, 'token': token}
    agents = []
    for index, name in enumerate(('a', 'b', 'c', 'd', 'race', 'decisions', 'cancel', 'recovery')):
        agent = {'id': identity(), 'token': secrets.token_urlsafe(40), 'work': identity(), 'name': name}
        host = hosts['host2' if name == 'd' else 'host1']
        agent.update(host=host, workspace=ids['workspace2' if name == 'c' else 'workspace1'])
        store.principal(owner, agent['id'], 'SYNTHETIC worker ' + name, 'agent', agent['token'])
        store.membership(owner, ids['room'], agent['id'], {'can_read': True, 'can_post': True, 'can_request': True}, 0)
        store.placement(owner, host['id'], 'SYNTHETIC ' + host['id'], 'fixture-identity-proof', {}, agent['id'])
        if not sql('SELECT 1 FROM hb_host_admission WHERE host=%s', (host['id'],)):
            sql("""INSERT INTO hb_host_admission(host,authority,controller,machine_identity,
                max_concurrent,reserved_interactive,memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
                VALUES(%s,%s,%s,'synthetic',16,0,1,0,300,300)""", (host['id'], ids['authority'], host['controller']))
        agents.append(agent)
    request = store.post(owner, ids['room'], identity(), {'kind': 'request', 'audience': 'room',
        'recipientId': credentials['Global']['principalId'], 'body': 'SYNTHETIC fixture goal source; no real human order.'})
    goal = store.workflow(owner, 'goal.create', ids['room'], identity(), {'id': ids['goal'],
        'sourceEventId': request['event']['id'], 'title': 'SYNTHETIC physical claims',
        'objective': 'Canonical substrate qualification only', 'acceptance': 'No native proof', 'reviewBudget': 0})
    store.workflow(owner, 'goal.cooperation', ids['goal'], identity(), {'revision': goal['goalRevision'],
        'cooperationRevision': 0, 'policy': 'leader_award', 'executorIds': [a['id'] for a in agents], 'reviewerId': None})
    for agent in agents:
        revision = sql('SELECT revision FROM hb_goal WHERE id=%s', (ids['goal'],))['revision']
        store.workflow(owner, 'goal.work', ids['goal'], identity(), {'id': agent['work'], 'revision': revision,
            'roomId': ids['room'], 'ownerId': agent['id'], 'specification': 'SYNTHETIC actual SQL scope',
            'acceptance': 'Substrate assertions only', 'dependencies': []})
        assignment = store.workflow(owner, 'work.assign', agent['work'], identity(), {'revision': 1})
        delivery, request_id = assignment['deliveryId'], assignment['requestEventId']
        context, thread = digest(agent['id']), 'synthetic-thread-' + agent['id']
        claim, turn = 'synthetic-claim-' + delivery, 'synthetic-turn-' + delivery
        sql("""UPDATE hb_delivery SET state='running',host=%s,generation=1,claim_id=%s,
            native_thread=%s,native_turn=%s WHERE id=%s""", (agent['host']['id'], claim, thread, turn, delivery))
        sql('INSERT INTO hb_agent_context(principal,authority,context_id,native_thread,host,workspace_id) VALUES(%s,%s,%s,%s,%s,%s)',
            (agent['id'], ids['authority'], context, thread, agent['host']['id'], agent['workspace']))
        sql('INSERT INTO hb_agent_execution VALUES(%s,1,%s,%s,%s,%s,1,%s)',
            (delivery, agent['id'], context, thread, agent['workspace'], digest(delivery)))
        agent['executor'] = identity()
        sql("""INSERT INTO hb_executor(id,authority,delivery,agent,host,generation,claim_id,descriptor,
            descriptor_digest,state,controller,evidence,observed_at) VALUES(%s,%s,%s,%s,%s,1,%s,'{}',%s,
            'live',%s,%s,clock_timestamp())""", (agent['executor'], ids['authority'], delivery, agent['id'],
            agent['host']['id'], claim, 'd' * 64, agent['host']['controller'],
            Jsonb({'syntheticFixtureOnly': True, 'reason': 'matched-live-incarnation', 'serviceBindingDigest': 'e' * 64})))
        agent['source'] = {'schema': 'hollywood-native-source/v2', 'authorityId': ids['authority'],
            'agentId': agent['id'], 'hostId': agent['host']['id'], 'deliveryId': delivery,
            'claimId': claim, 'generation': 1, 'requestId': request_id, 'nativeThread': thread,
            'nativeTurn': turn, 'callId': identity(), 'tool': 'coordination_act',
            'argumentsDigest': 'a' * 64, 'roleRevision': 1, 'contextId': context, 'nativeGoalId': None}
    a, b, c, d, race_agent, decision_agent, cancel_agent, recovery = agents
    baseline = {table: sql('SELECT * FROM ' + table + ' ORDER BY 1', many=True) for table in
                ('hb_work_attempt', 'hb_agent_context', 'hb_agent_execution')}
    baseline_deliveries = sql('SELECT count(*) AS n FROM hb_delivery')['n']
    checks = []
    resources = {}
    for agent in agents:
        key = (agent['host']['id'], agent['workspace'])
        if key not in resources:
            resource = {'id': identity(), 'root': out / 'roots' / identity(), 'host': agent['host']['id']}
            resource['root'].mkdir(parents=True)
            st = resource['root'].stat()
            resource['identity'] = digest([st.st_dev, st.st_ino])
            resources[key] = resource
        agent['resource'] = resources[key]

    def attest(payload):
        from hollywood_backbone.physical_workspaces import paths_for
        agent = next(a for a in agents if a['id'] == payload['source']['agentId'])
        resource = agent['resource']
        with store.pool.connection() as connection:
            paths = sorted(set(paths_for(connection, payload)))
        payload['target'] = {'schema': 'hollywood-physical-target/v1', 'workspaceId': resource['id'],
            'workspaceRevision': 1, 'grantRevision': 1, 'environmentSha256': 'c' * 64,
            'rootIdentitySha256': resource['identity'], 'resolution': 'beneath-no-symlinks/v1',
            'actionDigest': digest({k: v for k,v in payload.items() if k != 'target'}),
            'paths': [{'relativePath': path, 'resolvedPath': str(resource['root']) + '/' + path} for path in paths]}
        return payload
    runner = web.AppRunner(application(store))
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v3/'
    trace = []
    async with ClientSession() as client:
        async def http(token, method, path, body=None, status=200, headers=None):
            async with client.request(method, url + path, json=body,
                headers={'Authorization': 'Bearer ' + token, **(headers or {})}) as response:
                value = await response.json()
                trace.append({'method': method, 'path': path, 'status': response.status, 'result': value})
                with (out / 'http-trace.jsonl').open('a') as output:
                    output.write(json.dumps(trace[-1], default=str) + '\n')
                assert response.status == status, (path, response.status, status, value)
                return value

        def command(agent, action='acquire', claim=None, revision=0, **extra):
            result = dict(action=action, workId=agent['work'], workRevision=2,
                          claimId=claim or identity(), claimRevision=revision, briefingSequence=0,
                          note='SYNTHETIC substrate effect review; no native proof')
            if action == 'acquire':
                result.update(relativePath='src/item.py', kind='file', sourceRevision='a' * 40,
                              contentSha256='b' * 64, leaseSeconds=300)
            if action in ('renew', 'accept'):
                result['leaseSeconds'] = 300
            if action in ('release', 'handoff'):
                result['effectDisposition'] = 'reviewed'
            result.update(extra)
            return result

        def envelope(agent, cmd):
            return attest({'operationId': identity(), 'bridgeRevision': 1,
                'source': {**agent['source'], 'callId': identity()}, 'executorId': agent['executor'],
                'descriptorDigest': 'd' * 64, 'serviceBindingDigest': 'e' * 64,
                'channel': 'captured-app-server-rpc/v1', 'command': cmd})

        async def call(agent, cmd=None, body=None, status=200, token=None):
            return await http(token or agent['host']['token'], 'POST', 'collaboration/path-claims',
                              body or envelope(agent, cmd or command(agent)), status)

        try:
            assert sql('SELECT count(*) AS n FROM hb_collaboration_bridge')['n'] == 0
            await call(a, status=403)
            for host in hosts.values():
                policy = {'operationId': identity(), 'revision': 0, 'controllerId': host['controller'],
                    'state': 'active', 'protocol': 'canonical-edit-plans/v2',
                    'adapterSha256': 'a' * 64, 'enrollmentSha256': 'b' * 64}
                created = await http(credentials['Owner']['token'], 'PUT', f"hosts/{host['id']}/collaboration-bridge", policy)
                assert await http(credentials['Owner']['token'], 'PUT', f"hosts/{host['id']}/collaboration-bridge", policy) == created
                policy_read = await http(host['token'], 'GET', f"hosts/{host['id']}/collaboration-bridge")
                assert policy_read['patchPermits'] is True and policy_read['nativeChildAdmission'] is False
            for resource in resources.values():
                await http(credentials['Owner']['token'], 'PUT',
                    f"hosts/{resource['host']}/physical-workspaces/{resource['id']}", {
                        'operationId': identity(), 'revision': 0, 'state': 'active', 'rootPath': str(resource['root']),
                        'environmentSha256': 'c' * 64, 'rootIdentitySha256': resource['identity'],
                        'registrationSha256': 'a' * 64})
            for agent in agents:
                await http(credentials['Owner']['token'], 'PUT',
                    f"physical-workspaces/{agent['resource']['id']}/grants/{agent['work']}", {
                        'operationId': identity(), 'revision': 0, 'workspaceRevision': 1, 'workRevision': 2, 'state': 'active'})
            await call(a, token=a['token'], status=403)
            await call(a, token=hosts['host2']['token'], status=403)
            spoofed = envelope(a, command(a))
            await http(a['token'], 'POST', 'collaboration/path-claims', spoofed, 403,
                       {'X-Hollywood-Native-Source': json.dumps(a['source'])})
            await call(a, body={**spoofed, 'hostId': ids['host1']}, status=400)
            checks.append('Explicit bridge enrollment; worker bearer plus complete source fields, wrong controller and model physical identity rejected')

            # A read must reveal a revision, not demand that the caller guess it.
            probe = await call(a, command(a, relativePath='src/inspection.py'))
            probe_id = probe['claim']['id']
            renewed = await call(a, command(a, 'renew', probe_id, 1))
            assert renewed['claim']['revision'] == 2
            before_inspection = sql('SELECT * FROM hb_path_claim WHERE id=%s', (probe_id,))
            read_wire = envelope(a, command(a, 'inspect', probe_id, 0))
            inspected = await call(a, body=read_wire)
            assert inspected['claim'] == json.loads(json.dumps(before_inspection, default=str))
            assert inspected['patchPermitIssued'] is False and inspected['newExecutionAdmitted'] is False
            assert sql('SELECT * FROM hb_path_claim WHERE id=%s', (probe_id,)) == before_inspection
            await call(a, command(a, 'renew', probe_id, 1), status=409)
            assert sql('SELECT * FROM hb_path_claim WHERE id=%s', (probe_id,)) == before_inspection
            shared_inspection = await call(b, command(b, 'inspect', probe_id, 0))
            assert shared_inspection['claim']['owner'] == a['id']
            assert shared_inspection['claim']['revision'] == 2
            assert shared_inspection['leaseUsableAtReceipt'] is False
            assert shared_inspection['patchPermitIssued'] is False
            assert sql('SELECT * FROM hb_path_claim WHERE id=%s', (probe_id,)) == before_inspection
            # Explicitly synthetic foreign-Goal fixture state, with no native
            # or workflow admission. Same root/room alone must not authorize it.
            other_goal, other_work = identity(), identity()
            sql("""INSERT INTO hb_goal SELECT
                (jsonb_populate_record(NULL::hb_goal, to_jsonb(g) ||
                 jsonb_build_object('id',%s::text))).* FROM hb_goal g WHERE g.id=%s""",
                (other_goal, ids['goal']))
            sql("""INSERT INTO hb_work SELECT
                (jsonb_populate_record(NULL::hb_work, to_jsonb(w) ||
                 jsonb_build_object('id',%s::text,'goal',%s::text,'state','planned',
                                    'delivery',NULL,'request_event',NULL))).*
                FROM hb_work w WHERE w.id=%s""", (other_work, other_goal, a['work']))
            sql('UPDATE hb_path_claim SET work=%s WHERE id=%s', (other_work, probe_id))
            await call(b, command(b, 'inspect', probe_id, 0), status=403)
            sql('UPDATE hb_path_claim SET work=%s WHERE id=%s', (a['work'], probe_id))
            assert sql('SELECT * FROM hb_path_claim WHERE id=%s', (probe_id,)) == before_inspection
            await call(b, command(b, 'renew', probe_id, 2), status=403)
            await call(b, command(b, 'release', probe_id, 2), status=403)
            sql('UPDATE hb_membership SET can_read=false WHERE room=%s AND principal=%s', (ids['room'], b['id']))
            await call(b, command(b, 'inspect', probe_id, 0), status=403)
            sql('UPDATE hb_membership SET can_read=true WHERE room=%s AND principal=%s', (ids['room'], b['id']))
            sql("UPDATE hb_physical_grant SET state='revoked' WHERE workspace=%s AND work=%s", (b['resource']['id'], b['work']))
            await call(b, command(b, 'inspect', probe_id, 0), status=403)
            sql("UPDATE hb_physical_grant SET state='active' WHERE workspace=%s AND work=%s", (b['resource']['id'], b['work']))
            await call(c, command(c, 'inspect', probe_id, 0), status=403)
            await call(a, command(a, 'release', probe_id, 2))
            assert await call(a, body=read_wire) == inspected
            assert sql('SELECT state FROM hb_path_claim WHERE id=%s', (probe_id,))['state'] == 'released'
            checks.append('Inspection reveals current revision without claim CAS; no claim mutation, stale mutation still409, same-Goal collaborator inspection has no lease; foreign mutation/revoked read/grant/namespace denied; original inspection remains historical')

            original = envelope(a, command(a))
            receipt = await call(a, body=original)
            claim_id = receipt['claim']['id']
            assert receipt['patchPermitIssued'] is False and receipt['newExecutionAdmitted'] is False
            assert await call(a, body=original) == receipt
            changed = copy.deepcopy(original)
            changed['command']['note'] = 'Changed original operation'
            await call(a, body=changed, status=409)
            recovered = await http(a['host']['token'], 'GET', f"collaboration/commands/{a['id']}/{original['operationId']}")
            assert recovered['acceptedResult'] == receipt and recovered['receiptOnly'] is True
            assert recovered['wireDigest'] == digest(recovered['wireRequest'])
            for bad in ('src//x', '../x', '/src/x', 'C:/x', 'src\\x', 'x\x00y'):
                await call(a, command(a, relativePath=bad), status=400)
            overlap_wire = envelope(b, command(b))
            unchanged_claim = sql('SELECT * FROM hb_path_claim WHERE id=%s', (claim_id,))
            overlap = await call(b, body=overlap_wire, status=409)
            assert overlap['schema'] == 'hollywood-collaboration-rejection/v1'
            assert overlap['state'] == 'rejected' and overlap['code'] == 'COLLAB_PATH_CLAIM_CONFLICT'
            assert overlap['newMutationPermitIssued'] is False and overlap['newExecutionAdmitted'] is False
            assert overlap['priorFilesystemEffects'] == 'not_inferred'
            assert sql('SELECT * FROM hb_path_claim WHERE id=%s', (claim_id,)) == unchanged_claim
            assert not sql('SELECT 1 FROM hb_path_claim WHERE id=%s', (overlap_wire['command']['claimId'],))
            assert not sql('SELECT 1 FROM hb_native_effect WHERE principal=%s AND operation=%s',
                           (b['id'], overlap_wire['operationId']))
            assert await call(b, body=overlap_wire, status=409) == overlap
            original_rejection = await http(b['host']['token'], 'GET',
                f"collaboration/commands/{b['id']}/{overlap_wire['operationId']}")
            assert original_rejection['acceptedResult'] == overlap
            assert original_rejection['eventId'] == overlap['eventId']
            assert sql("SELECT count(*) n FROM hb_event WHERE data->'rejectionReceipt'->>'operationId'=%s",
                       (overlap_wire['operationId'],))['n'] == 1
            from hollywood_backbone.execution_evidence import snapshot as evidence_snapshot
            with store.pool.connection() as connection:
                selected = evidence_snapshot(connection, store, store.actor(connection, b['id']),
                    {'version': 1, 'deliveryId': b['source']['deliveryId'],
                     'eventIds': [overlap['eventId']], 'contextOperationIds': []})
            assert selected['records'][0]['event']['data']['rejectionReceipt'] == overlap
            altered = copy.deepcopy(overlap_wire);altered['command']['note'] = 'Changed rejected original'
            await call(b, body=altered, status=409)
            assert sql('SELECT accepted_result FROM hb_command WHERE principal=%s AND operation=%s',
                       (b['id'], overlap_wire['operationId']))['accepted_result'] == overlap
            checks.append('Typed overlap rejection commits one authority evidence/command receipt without claim/effect/permit mutation; same operation is stable and changed wire refused')
            await call(c, command(c))
            await call(d, command(d))
            checks.append('Stable canonical receipt/recovery and changed-payload conflict; same path conflicts only in exact host/workspace; canonical path validation')

            for field, value in (('bridgeRevision', 99), ('descriptorDigest', 'f' * 64), ('serviceBindingDigest', 'f' * 64)):
                invalid = envelope(a, command(a, relativePath='other/' + field))
                invalid[field] = value
                await call(a, body=invalid, status=409)
            for field, value in (('generation', 2), ('claimId', 'stale'), ('contextId', 'f' * 64), ('nativeTurn', 'stale')):
                invalid = envelope(a, command(a, relativePath='other/' + field))
                invalid['source'][field] = value
                await call(a, body=invalid, status=409)
            wrong_work = command(a, relativePath='other/work', workId=b['work'])
            await call(a, wrong_work, status=403)
            await call(a, command(a, relativePath='other/revision', workRevision=999), status=409)
            checks.append('Current bridge/descriptor/service binding, native generation/claim/context/turn, exact work owner and work revision fenced')

            sql("UPDATE hb_path_claim SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (claim_id,))
            await call(a, command(a, 'renew', claim_id, 1), status=409)
            await call(b, command(b), status=409)
            inspected = await call(a, command(a, 'inspect', claim_id, 1))
            assert inspected['leaseUsableAtReceipt'] is False
            await call(a, command(a, 'release', claim_id, 1, effectDisposition='unknown'), status=409)
            release = envelope(a, command(a, 'release', claim_id, 1))
            released = await call(a, body=release)
            assert released['claim']['state'] == 'released'
            assert await call(a, body=release) == released
            await call(a, command(a, 'renew', claim_id, 2), status=409)
            checks.append('Expired lease retains ownership and blocks takeover; unknown effects hold; explicit reviewed release; released identity cannot resume')

            shared = await call(a, command(a, relativePath='src/shared.py'))
            shared_id = shared['claim']['id']
            await call(a, command(a, 'handoff', shared_id, 1, targetWorkId=d['work'], targetWorkRevision=2), status=403)
            offered = await call(a, command(a, 'handoff', shared_id, 1, targetWorkId=b['work'], targetWorkRevision=2))
            assert offered['claim']['state'] == 'handoff' and offered['leaseUsableAtReceipt'] is False
            await call(a, command(a, 'renew', shared_id, 2), status=409)
            sql('UPDATE hb_membership SET can_post=false WHERE room=%s AND principal=%s', (ids['room'], a['id']))
            await call(b, command(b, 'accept', shared_id, 2), status=403)
            sql('UPDATE hb_membership SET can_post=true WHERE room=%s AND principal=%s', (ids['room'], a['id']))
            sql("UPDATE hb_delivery SET state='unknown' WHERE id=%s", (a['source']['deliveryId'],))
            await call(b, command(b, 'accept', shared_id, 2), status=409)
            sql("UPDATE hb_delivery SET state='running' WHERE id=%s", (a['source']['deliveryId'],))
            sql("UPDATE hb_executor SET state='unknown' WHERE delivery=%s", (a['source']['deliveryId'],))
            await call(b, command(b, 'accept', shared_id, 2), status=409)
            sql("UPDATE hb_executor SET state='live' WHERE delivery=%s", (a['source']['deliveryId'],))
            sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (a['source']['deliveryId'],))
            await call(b, command(b, 'accept', shared_id, 2), status=409)
            sql("UPDATE hb_delivery SET state='running' WHERE id=%s", (a['source']['deliveryId'],))
            accepted = await call(b, command(b, 'accept', shared_id, 2))
            assert accepted['claim']['work'] == b['work'] and accepted['claim']['owner'] == b['id']
            assert accepted['claim']['origin_delivery'] == b['source']['deliveryId']
            await call(a, command(a, 'release', shared_id, 3), status=403)
            await call(b, command(b, 'release', shared_id, 2), status=409)
            await call(b, command(b, 'release', shared_id, 3))
            checks.append('Explicit same-namespace handoff offer/accept into existing canonical peer work; cross-host transfer denied; prior owner and stale CAS denied')

            directory = await call(a, command(a, relativePath='literal_%', kind='directory'))
            await call(b, command(b, relativePath='literal_%/x.py'), status=409)
            await call(b, command(b, relativePath='literal_abc/x.py'))
            await call(a, command(a, 'renew', directory['claim']['id'], 1))
            checks.append('Directory overlap is segment-exact, including literal SQL wildcard characters; valid renewal preserves source revision/content')

            same = envelope(race_agent, command(race_agent, relativePath='race/same'))
            results = await asyncio.gather(call(race_agent, body=same), call(race_agent, body=same))
            assert results[0] == results[1]
            assert sql('SELECT count(*) AS n FROM hb_path_claim WHERE id=%s', (same['command']['claimId'],))['n'] == 1
            x = envelope(a, command(a, relativePath='race/overlap'))
            y = envelope(b, command(b, relativePath='race/overlap'))
            async def contender(agent, body):
                async with client.post(url + 'collaboration/path-claims', json=body,
                    headers={'Authorization': 'Bearer ' + agent['host']['token']}) as response:
                    value = await response.json()
                    with (out / 'race-trace.jsonl').open('a') as output:
                        output.write(json.dumps({'kind': 'overlap', 'status': response.status, 'result': value}) + '\n')
                    return response.status
            assert sorted(await asyncio.gather(contender(a, x), contender(b, y))) == [200, 409]
            checks.append('Actual PostgreSQL concurrency: same operation produces one stable receipt; overlapping peer acquisitions have one winner')

            decision = {'id': identity(), 'key': 'editing', 'revision': 0, 'goalId': ids['goal'],
                'state': 'active', 'body': 'SYNTHETIC current source decision', 'sourceEventId': request['event']['id']}
            store.decision_write(owner, ids['room'], identity(), decision)
            await call(decision_agent, command(decision_agent, relativePath='decisions/stale'), status=409)
            head = sql('SELECT next_seq FROM hb_authority WHERE id=%s', (ids['authority'],))['next_seq']
            current = envelope(decision_agent, command(decision_agent, relativePath='decisions/current', briefingSequence=head))
            await call(decision_agent, body=current)
            store.decision_write(owner, ids['room'], identity(), {**decision, 'revision': 1, 'state': 'conflicted'})
            await call(decision_agent, body=current, status=409)
            store.decision_write(owner, ids['room'], identity(), {**decision, 'revision': 2, 'state': 'active'})
            head = sql('SELECT next_seq FROM hb_authority WHERE id=%s', (ids['authority'],))['next_seq']
            checks.append('Stale briefing and conflicted current decision reject new effects and receipt replay')

            cancellation = envelope(cancel_agent, command(cancel_agent, relativePath='cancel/race', briefingSequence=head))
            async def cancel():
                return await http(credentials['Owner']['token'], 'POST', f"work/{cancel_agent['work']}/cancel",
                                  {'operationId': identity(), 'revision': 2, 'note': 'SYNTHETIC cancellation race'})
            result = await asyncio.gather(contender(cancel_agent, cancellation), cancel())
            assert result[0] in (200, 409) and result[1]['state'] in ('cancel_requested', 'cancelled')
            await call(cancel_agent, body=cancellation, status=409)
            checks.append('Actual claim-versus-cancel race serializes; no subsequent effects admitted from cancelled participation')

            cleanup_body = envelope(recovery, command(recovery, relativePath='cleanup/file', briefingSequence=head))
            cleanup_claim = await call(recovery, body=cleanup_body)
            sql("UPDATE hb_goal SET state='paused' WHERE id=%s", (ids['goal'],))
            await call(recovery, command(recovery, 'renew', cleanup_claim['claim']['id'], 1, briefingSequence=head), status=409)
            # Later ordinary participation may release, but cannot edit an old
            # completed assignment. SQL transition below remains synthetic input.
            sql("UPDATE hb_goal SET state='active' WHERE id=%s", (ids['goal'],))
            prior_delivery = recovery['source']['deliveryId']
            sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (prior_delivery,))
            await call(recovery, body=cleanup_body, status=409)
            stable = await http(recovery['host']['token'], 'GET', f"collaboration/commands/{recovery['id']}/{cleanup_body['operationId']}")
            assert stable['acceptedResult'] == cleanup_claim and stable['receiptOnly'] is True
            denied = await http(recovery['host']['token'], 'GET', f"collaboration/commands/{recovery['id']}/{current['operationId']}", status=403)
            assert denied['error'] == 'Access denied'
            later = store.post(owner, ids['room'], identity(), {'kind': 'request', 'audience': 'room',
                'recipientId': recovery['id'], 'body': 'SYNTHETIC later normal cleanup participation; no work assignment.'})
            later_delivery = later['deliveries'][0]['id']
            later_claim, later_turn = 'synthetic-claim-' + later_delivery, 'synthetic-turn-' + later_delivery
            sql("""UPDATE hb_delivery SET state='running',host=%s,generation=1,claim_id=%s,
                native_thread=%s,native_turn=%s WHERE id=%s""", (recovery['host']['id'], later_claim,
                recovery['source']['nativeThread'], later_turn, later_delivery))
            sql('INSERT INTO hb_agent_execution VALUES(%s,1,%s,%s,%s,%s,1,%s)', (later_delivery,
                recovery['id'], recovery['source']['contextId'], recovery['source']['nativeThread'],
                recovery['workspace'], digest(later_delivery)))
            recovery['executor'] = identity()
            sql("""INSERT INTO hb_executor(id,authority,delivery,agent,host,generation,claim_id,descriptor,
                descriptor_digest,state,controller,evidence,observed_at) VALUES(%s,%s,%s,%s,%s,1,%s,'{}',%s,
                'live',%s,%s,clock_timestamp())""", (recovery['executor'], ids['authority'], later_delivery,
                recovery['id'], recovery['host']['id'], later_claim, 'd' * 64, recovery['host']['controller'],
                Jsonb({'syntheticFixtureOnly': True, 'reason': 'matched-live-incarnation', 'serviceBindingDigest': 'e' * 64})))
            recovery['source'].update(deliveryId=later_delivery, requestId=later['event']['id'],
                                      claimId=later_claim, nativeTurn=later_turn)
            await call(recovery, command(recovery, relativePath='cleanup/replay', briefingSequence=head), status=409)
            sql("UPDATE hb_goal SET state='paused' WHERE id=%s", (ids['goal'],))
            await call(recovery, command(recovery, 'release', cleanup_claim['claim']['id'], 1, briefingSequence=head), status=409)
            sql("UPDATE hb_executor SET state='stopped' WHERE delivery=%s", (prior_delivery,))
            cleanup = await call(recovery, command(recovery, 'release', cleanup_claim['claim']['id'], 1, briefingSequence=head))
            assert cleanup['claim']['state'] == 'released'
            sql("UPDATE hb_goal SET state='active' WHERE id=%s", (ids['goal'],))
            checks.append('Later normal same-context cleanup can explicitly release paused work, cannot edit/re-admit old work; receipt recovery after original completion is read-only')

            sql('UPDATE hb_membership SET can_post=false WHERE room=%s AND principal=%s', (ids['room'], a['id']))
            await call(a, body=original, status=403)
            sql('UPDATE hb_membership SET can_post=true WHERE room=%s AND principal=%s', (ids['room'], a['id']))
            sql('UPDATE hb_placement SET enabled=false WHERE agent=%s', (a['id'],))
            await call(a, body=original, status=409)
            sql('UPDATE hb_placement SET enabled=true WHERE agent=%s', (a['id'],))
            checks.append('Current posting-right and placement revocation fence even original receipt replay')

            revoked = {'operationId': identity(), 'revision': 1, 'controllerId': a['host']['controller'],
                'state': 'revoked', 'protocol': 'canonical-edit-plans/v2', 'adapterSha256': 'a' * 64, 'enrollmentSha256': 'b' * 64}
            await http(credentials['Owner']['token'], 'PUT', f"hosts/{a['host']['id']}/collaboration-bridge", revoked)
            await call(a, body=original, status=403)
            await http(a['host']['token'], 'GET', f"collaboration/commands/{a['id']}/{original['operationId']}", status=403)
            checks.append('Explicit bridge revocation fences effects and receipt recovery')

            for table, rows in baseline.items():
                actual = sql('SELECT * FROM ' + table + ' ORDER BY 1', many=True)
                if table == 'hb_agent_execution':
                    actual = [row for row in actual if str(row['delivery']) != later_delivery]
                assert actual == rows
            assert sql('SELECT count(*) AS n FROM hb_delivery')['n'] == baseline_deliveries + 1
            assert sql('SELECT count(*) AS n FROM hb_work_report_reconciliation')['n'] == 0
            accepted = sql("""SELECT count(*) n FROM hb_command
                WHERE wire_request->>'path'='collaboration/path-claims'
                  AND accepted_result->>'schema' IS DISTINCT FROM 'hollywood-collaboration-rejection/v1'""")['n']
            assert accepted == sql("SELECT count(*) n FROM hb_native_effect WHERE path='collaboration/path-claims'")['n']
            assert sql("""SELECT count(*) n FROM hb_command c JOIN hb_native_effect e
                ON e.principal=c.principal AND e.operation=c.operation
                WHERE c.accepted_result->>'schema'='hollywood-collaboration-rejection/v1'""")['n'] == 0
            assert sql("""SELECT count(*) n FROM hb_command c LEFT JOIN hb_event e ON e.id=c.event
                WHERE c.accepted_result->>'schema'='hollywood-collaboration-rejection/v1'
                  AND (e.data->'rejectionReceipt' IS DISTINCT FROM c.accepted_result)""")['n'] == 0
            checks.append('Original attempts/context/bindings unchanged; zero new work deliveries, report reconciliations or native admissions; existing command/effect ledgers reused')
            (out / 'result.json').write_text(json.dumps({'state': 'passed', 'groups': checks,
                'actualPostgreSQL': True, 'actualAuthenticatedHTTP': True, 'actualNativeProof': False,
                'nativeLifecycle': 'SYNTHETIC ONLY', 'httpRecords': len(trace)}, indent=2) + '\n')
            print(json.dumps({'state': 'passed', 'groups': len(checks), 'httpRecords': len(trace), 'nativeProof': False}))
        finally:
            await runner.cleanup()
            store.close()


if __name__ == '__main__':
    asyncio.run(main())
