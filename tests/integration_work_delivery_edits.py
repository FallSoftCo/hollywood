"""Changed Work-backed direct editing mechanisms, real PostgreSQL and HTTP.

All native/executor metadata is explicitly SYNTHETIC. Fixture RAM byte writes
are not native/OS callback proof. The fulfilled original native task is not run.
"""
import asyncio
import copy
import json
import secrets

from psycopg.types.json import Jsonb

from edit_plan_fixture import Fixture, uid, sha
from hollywood_backbone.store import digest, json_value


async def main():
    f = Fixture()
    await f.start()
    owner, actor = f.credentials['Owner']['token'], f.agents['i']
    resource = f.resources[actor['workspace']]
    checks, faults = [], []
    contexts_before = f.sql('SELECT * FROM hb_agent_context ORDER BY principal', many=True)
    work_before = f.sql('SELECT * FROM hb_work ORDER BY id', many=True)

    async def grant(agent, state='active', write=True):
        prior = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                      (resource['id'], agent['id']))
        return await f.http(owner, 'PUT', f"physical-workspaces/{resource['id']}/agent-grants/{agent['id']}", {
            'operationId': uid(), 'revision': prior['revision'] if prior else 0, 'workspaceRevision': resource['revision'],
            'executionHost': agent['host']['id'], 'state': state, 'canRead': True, 'canWrite': write})

    def case(path, existing=False):
        actual = resource['root'] / path
        if not existing:
            actual.parent.mkdir(parents=True, exist_ok=True)
            actual.write_bytes(b'base\n')
        before = actual.read_bytes()
        after = before + b'changed\n'
        return {'permitId': uid(), 'patchSha256': sha(('SYNTHETIC:' + path).encode()),
                'files': [{'relativePath': path, 'beforeSha256': sha(before), 'beforeSize': len(before),
                           'afterSha256': sha(after), 'afterSize': len(after)}], 'after': after}

    def frame(agent, item, action='preflight', permit=None, source=None, outcome=None):
        observed = []
        for file in item['files']:
            target = resource['root'] / file['relativePath']
            data = target.read_bytes() if target.exists() else None
            observed.append({'relativePath': file['relativePath'],
                             'sha256': sha(data) if data is not None else 'absent',
                             'size': len(data) if data is not None else 0})
        command = {'action': action, 'permitId': item['permitId'],
            'briefingSequence': f.sql('SELECT next_seq FROM hb_authority WHERE id=%s', (f.ids['authority'],))['next_seq'],
            'note': 'SYNTHETIC Work/editing separation qualification', 'sourceRevision': None,
            'patchSha256': item['patchSha256'], 'filesObserved': observed}
        if action == 'preflight':
            command.update(files=item['files'], leaseSeconds=30)
        else:
            command['permitRevision'] = permit['revision']
        if outcome:
            command['outcome'] = outcome
        value = {'schema': 'canonical-delivery-edits/v1', 'scope': 'delivery', 'operationId': uid(),
            'bridgeRevision': 1, 'source': source or {**agent['source'], 'callId': uid(), 'tool': 'apply_patch'},
            'executorId': agent['executor'], 'descriptorDigest': 'd'*64, 'serviceBindingDigest': 'e'*64,
            'channel': 'captured-app-server-rpc/v1', 'command': command}
        access = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                       (resource['id'], agent['id']))
        value['target'] = {'schema': 'hollywood-physical-target/v1', 'workspaceId': resource['id'],
            'workspaceRevision': resource['revision'], 'grantRevision': access['revision'] if access else 1,
            'environmentSha256': resource['environment'], 'rootIdentitySha256': resource['identity'],
            'resolution': 'beneath-no-symlinks/v1', 'actionDigest': digest(value),
            'paths': [{'relativePath': file['relativePath'], 'resolvedPath': str(resource['root'] / file['relativePath'])}
                      for file in item['files']]}
        return value

    async def call(agent, value, status=200):
        return await f.http(agent['host']['token'], 'POST', 'collaboration/delivery-edits', value, status=status)

    async def reserve(item, agent=actor, status=200):
        body = frame(agent, item)
        result = await call(agent, body, status)
        return body, result['receipt']['permit'] if status == 200 else None

    async def phase(item, body, permit, action, agent=actor, outcome=None, status=200):
        value = frame(agent, item, action, permit, source=copy.deepcopy(body['source']), outcome=outcome)
        result = await call(agent, value, status)
        return value, result

    async def conclusive(item, agent=actor, applied=False):
        body, permit = await reserve(item, agent)
        begin_body, begun = await phase(item, body, permit, 'begin', agent)
        assert begun['admission']['mayApply'] and begun['admission']['oneShotOriginalResponseOnly']
        replay = await call(agent, begin_body)
        assert replay['replayed'] and replay['admission'] is None
        if applied:
            assert len(item['files']) == 1
            (resource['root'] / item['files'][0]['relativePath']).write_bytes(item['after'])
        _, settled = await phase(item, body, begun['receipt']['permit'], 'settle', agent,
                                 outcome='applied' if applied else 'notApplied')
        assert settled['receipt']['permit']['state'] == 'settled'
        assert not f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s', (item['permitId'],))
        return permit

    async def release_claim(identity):
        row = f.sql('SELECT * FROM hb_path_claim WHERE id=%s', (identity,))
        agent = next(agent for agent in f.agents.values() if agent['id'] == str(row['owner']))
        body = f.envelope(agent, {'action': 'release', 'workId': str(row['work']), 'workRevision': 2,
            'claimId': identity, 'claimRevision': row['revision'], 'briefingSequence': 0,
            'note': 'SYNTHETIC explicit release with no mutation', 'effectDisposition': 'reviewed'})
        body['source']['tool'] = 'coordination_act'
        return await f.http(agent['host']['token'], 'POST', 'collaboration/path-claims', body)

    async def fault(name, mutate, restore, status=409, after_begin=False):
        item = case('faults/' + name + '.txt')
        body, permit = await reserve(item)
        if after_begin:
            _, begun = await phase(item, body, permit, 'begin')
            permit = begun['receipt']['permit']
        original = f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (item['permitId'],))
        locks = f.sql('SELECT * FROM hb_edit_file_lock WHERE permit=%s', (item['permitId'],), many=True)
        mutate()
        try:
            await phase(item, body, permit, 'settle' if after_begin else 'begin',
                        outcome='notApplied' if after_begin else None, status=status)
            view = await f.http(actor['host']['token'], 'GET', 'collaboration/delivery-edits/' + item['permitId'])
            assert view['effectiveState'] == 'held' and not view['mayApply']
            assert f.sql('SELECT * FROM hb_edit_permit WHERE id=%s', (item['permitId'],)) == original
            assert f.sql('SELECT * FROM hb_edit_file_lock WHERE permit=%s', (item['permitId'],), many=True) == locks
            faults.append({'name': name, 'phase': 'settle' if after_begin else 'begin', 'status': status,
                'permitId': item['permitId'], 'originalState': original['state'], 'effectiveState': view['effectiveState'],
                'workAuthority': original['proof']['workAuthority'], 'locksPreserved': len(locks)})
        finally:
            # Restore synthetic fixture authority only for independent cases.
            # Never retry/consume the original permit after fault restoration.
            restore()

    try:
        capabilities = await f.http(owner, 'GET', 'hosts/' + actor['host']['id'] + '/collaboration-bridge')
        assert capabilities['workDeliveryEdits'] and capabilities['workDeliveryEditPolicy'] == 'canonical-work-delivery-edits/v1'
        assert capabilities['deliveryEditSchema'] == 'canonical-delivery-edits/v1'
        positive = case('work/direct.txt')
        await reserve(positive, status=403)  # Goal and per-work grant are not agent access.
        await grant(actor)
        await grant(f.agents['p'])
        permit = await conclusive(positive, applied=True)
        pin = permit['proof']['workAuthority']
        assert pin['workId'] == actor['work'] and pin['workRevision'] == 2
        assert pin['attemptId'] == actor['source']['deliveryId'] and pin['attemptNumber'] == 1
        assert pin['ownerId'] == actor['id'] and pin['requestId'] == actor['source']['requestId']
        assert pin['schema'] == 'canonical-work-delivery-edits/v1' and pin['placementRevision'] == 1
        assert not f.sql('SELECT 1 FROM hb_edit_plan') and not f.sql('SELECT 1 FROM hb_path_claim')
        assert f.sql('SELECT * FROM hb_work ORDER BY id', many=True) == work_before
        assert f.sql('SELECT * FROM hb_agent_context ORDER BY principal', many=True) == contexts_before
        targets = await f.http(actor['host']['token'], 'GET', 'collaboration/delivery-targets/' + actor['source']['deliveryId'])
        assert targets['targets'] and not targets['mayApply']
        checks.append('Actual admitted Work edits directly with independent agent access, canonical attempt pin, one-shot begin/replay/settle, no file plan and no task completion')

        # Routing a reviewer to readable work must never grant mutation rights.
        readonly = f.agents['outsider']
        await grant(readonly, write=False)
        pointer = 'agents/' + readonly['id'] + '/resource-defaults/' + readonly['host']['id']
        async def choose_readonly():
            current = await f.http(owner, 'GET', pointer)
            access = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                           (resource['id'], readonly['id']))
            return await f.http(owner, 'PUT', pointer, {'operationId': uid(),
                'revision': current['selectionRevision'], 'state': 'selected',
                'workspaceId': resource['id'], 'workspaceRevision': resource['revision'],
                'grantRevision': access['revision']})
        chosen = await choose_readonly()
        assert capabilities['readOnlyResourceDefaults'] is True
        assert chosen['effectiveState'] == 'selected' and not chosen['mayApply']
        assert chosen['resource']['grant']['can_read'] and not chosen['resource']['grant']['can_write']
        assert (await f.http(readonly['token'], 'GET', pointer)) == chosen
        attempted = case('readonly/forbidden.txt')
        await reserve(attempted, readonly, status=403)
        assert not f.sql('SELECT 1 FROM hb_edit_permit WHERE id=%s', (attempted['permitId'],))
        await grant(readonly, state='revoked', write=False)
        assert (await f.http(owner, 'GET', pointer))['holdReason'] == 'access-unavailable'
        await grant(readonly, write=False)
        assert (await f.http(owner, 'GET', pointer))['holdReason'] == 'selection-revisions-changed'
        assert (await choose_readonly())['effectiveState'] == 'selected'
        for enabled in (False, True):
            placement = f.sql('SELECT revision FROM hb_placement WHERE agent=%s', (readonly['id'],))
            await f.http(owner, 'PUT', 'placements/' + readonly['id'], {'operationId': uid(),
                'revision': placement['revision'], 'hostId': readonly['host']['id'], 'enabled': enabled})
            assert (await f.http(owner, 'GET', pointer))['effectiveState'] == 'held'
        assert (await choose_readonly())['effectiveState'] == 'selected'
        await reserve(case('readonly/still-forbidden.txt'), readonly, status=403)
        checks.append('Read-only defaults select/read without mutation authority; edit403, revoke/regrant revision holds and sticky pause/resume require fresh reviewed CAS')

        ordinary = {'id': uid(), 'token': secrets.token_urlsafe(40), 'host': actor['host'], 'name': 'ordinary',
            'privateWorkspace': uid(), 'workspace': actor['workspace'], 'root': resource['root'], 'work': None}
        f.store.principal(f.owner, ordinary['id'], 'SYNTHETIC ordinary compatibility', 'agent', ordinary['token'])
        f.store.membership(f.owner, f.ids['room'], ordinary['id'], {'can_read': True, 'can_post': True, 'can_request': True}, 0)
        f.store.placement(f.owner, actor['host']['id'], 'SYNTHETIC unchanged host', 'synthetic-identity-proof', {}, ordinary['id'])
        request = f.store.post(f.owner, f.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': ordinary['id'], 'body': 'SYNTHETIC baseline only; not original native task'})
        f.bind(ordinary, request['deliveries'][0]['id'], request['event']['id'])
        f.agents['ordinary'] = ordinary
        await grant(ordinary)
        baseline = await conclusive(case('ordinary/compatibility.txt'), ordinary)
        assert baseline['proof']['workAuthority'] is None
        assert not f.sql('SELECT 1 FROM hb_work WHERE owner=%s', (ordinary['id'],))
        checks.append('One ordinary non-Work compatibility case retains null Work authority and existing wire/one-shot behavior')

        # Review actual transitions: proposal claims, explicit release, ready
        # consent, and withdrawal. A draft never becomes an execution grant.
        scope = await f.case('scope-transitions')
        await f.propose(scope)
        contested = scope['manifest']['files'][1]  # Owned by integrator itself.
        await reserve(case(contested['relativePath'], existing=True), status=409)
        mixed = case('scope-transitions/free.txt')
        mixed['files'].append(case(contested['relativePath'], existing=True)['files'][0])
        await reserve(mixed, status=409)
        assert not f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s', (mixed['permitId'],))
        for file in scope['manifest']['files']:
            await release_claim(file['claimId'])
        assert (await f.view(scope))['plan']['state'] == 'collecting'
        await reserve(case(contested['relativePath'], existing=True), status=409)
        await f.ready(scope)
        assert (await f.view(scope))['plan']['state'] == 'ready'
        await reserve(case(contested['relativePath'], existing=True), status=409)
        await f.reserve(scope, status=409)  # Released claims still cannot authorize a plan.
        await conclusive(case('scope-transitions/outside.txt'))
        await f.call(scope['integrator'], f.command(scope, scope['integrator'], 'withdraw'))
        await conclusive(case(contested['relativePath'], existing=True))
        checks.append('Collecting/ready joint targets and mixed patches remain blocked even after claim release; explicit withdrawal retires scope without granting planned apply')

        planned = await f.case('planned-conflict')
        await f.propose(planned)
        await f.ready(planned)
        reservation, result = await f.reserve(planned)
        planned_permit = result['receipt']['permit']
        assert planned_permit['scope_kind'] == 'plan'
        await reserve(case(planned['manifest']['files'][0]['relativePath'], existing=True), status=409)
        planned_begin = await f.call(planned['integrator'], body=f.phase(planned, reservation, planned_permit, 'begin'))
        assert planned_begin['admission']['mayApply']
        planned_settle = await f.call(planned['integrator'], body=f.phase(planned, reservation,
            planned_begin['receipt']['permit'], 'settle', outcome='notApplied'))
        assert planned_settle['receipt']['permit']['state'] == 'settled'
        await reserve(case(planned['manifest']['files'][0]['relativePath'], existing=True), status=409)
        checks.append('Existing exact participant consent and planned one-use path remain intact; direct Work cannot bypass active or settled-but-unreleased file ownership')

        # Ordinary editing is authorized by the current Work's exclusive file
        # ownership; a joint plan is a separate collaboration commitment.
        async def own(item):
            file = item['files'][0]
            value = f.envelope(actor, {'action': 'acquire', 'workId': actor['work'],
                'workRevision': 2, 'claimId': uid(), 'claimRevision': 0,
                'briefingSequence': 0, 'note': 'SYNTHETIC owned native edit',
                'relativePath': file['relativePath'], 'kind': 'file',
                'sourceRevision': 'a' * 40, 'contentSha256': file['beforeSha256'],
                'leaseSeconds': 3600})
            value['source']['tool'] = 'coordination_act'
            return (await f.http(actor['host']['token'], 'POST',
                    'collaboration/path-claims', value))['claim']

        owned_item = case('owned/direct.txt')
        (resource['root'] / 'owned/direct.txt').unlink()
        owned_item['files'][0].update(beforeSha256='absent', beforeSize=0)
        owned_claim = await own(owned_item)
        owned_permit = await conclusive(owned_item, applied=True)
        advanced = f.sql('SELECT * FROM hb_path_claim WHERE id=%s', (owned_claim['id'],))
        assert advanced['revision'] == 2
        assert advanced['content_sha256'] == owned_item['files'][0]['afterSha256']
        assert owned_permit['proof']['ownershipClaims'][0]['claimId'] == owned_claim['id']
        settled_owned = f.sql('SELECT settlement FROM hb_edit_permit WHERE id=%s', (owned_item['permitId'],))
        assert len(settled_owned['settlement']['ownershipTransitions']) == 1
        await reserve(case('owned/direct.txt', existing=True), f.agents['p'], status=409)

        stale_claim_item = case('owned/changed.txt')
        stale_claim = await own(stale_claim_item)
        stale_body, stale_permit = await reserve(stale_claim_item)
        f.sql('UPDATE hb_path_claim SET revision=revision+1 WHERE id=%s', (stale_claim['id'],))
        await phase(stale_claim_item, stale_body, stale_permit, 'begin', status=409)
        assert f.sql('SELECT state FROM hb_edit_permit WHERE id=%s', (stale_permit['id'],))['state'] == 'reserved'
        assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s', (stale_permit['id'],))

        sibling_item = case('parallel/sibling-progress.txt')
        sibling_body, sibling_permit = await reserve(sibling_item)
        epoch = f.sql('SELECT control_revision FROM hb_goal WHERE id=%s', (f.ids['goal'],))['control_revision']
        # Exactly the board revision update made by a sibling Work transition.
        f.sql('UPDATE hb_goal SET revision=revision+1 WHERE id=%s', (f.ids['goal'],))
        assert f.sql('SELECT control_revision FROM hb_goal WHERE id=%s', (f.ids['goal'],))['control_revision'] == epoch
        _, sibling_begin = await phase(sibling_item, sibling_body, sibling_permit, 'begin')
        assert sibling_begin['admission']['mayApply']
        _, sibling_settle = await phase(sibling_item, sibling_body, sibling_begin['receipt']['permit'],
                                        'settle', outcome='notApplied')
        assert sibling_settle['receipt']['permit']['state'] == 'settled'
        checks.append('Owned Work file edit advances exact claim bytes/revision; foreign ownership and changed claim retain locks; sibling progress permits concurrent work while Goal control transitions still fence')

        work_id, delivery = actor['work'], actor['source']['deliveryId']
        await fault('owner', lambda: f.sql('UPDATE hb_work SET owner=%s WHERE id=%s', (f.agents['p']['id'], work_id)),
            lambda: f.sql('UPDATE hb_work SET owner=%s WHERE id=%s', (actor['id'], work_id)), 403)
        await fault('revision', lambda: f.sql('UPDATE hb_work SET revision=revision+1 WHERE id=%s', (work_id,)),
            lambda: f.sql('UPDATE hb_work SET revision=2 WHERE id=%s', (work_id,)))
        await fault('attempt-owner', lambda: f.sql('UPDATE hb_work_attempt SET owner=%s WHERE delivery=%s', (f.agents['p']['id'], delivery)),
            lambda: f.sql('UPDATE hb_work_attempt SET owner=%s WHERE delivery=%s', (actor['id'], delivery)))
        await fault('attempt-number', lambda: f.sql('UPDATE hb_work_attempt SET number=2 WHERE delivery=%s', (delivery,)),
            lambda: f.sql('UPDATE hb_work_attempt SET number=1 WHERE delivery=%s', (delivery,)))
        original_request = f.sql('SELECT data FROM hb_event WHERE id=%s', (actor['source']['requestId'],))['data']
        await fault('hidden-work', lambda: f.sql("UPDATE hb_event SET data=data-'workId' WHERE id=%s", (actor['source']['requestId'],)),
            lambda: f.sql('UPDATE hb_event SET data=%s WHERE id=%s', (Jsonb(original_request), actor['source']['requestId'])))
        await fault('cooperation', lambda: f.sql('DELETE FROM hb_cooperation_executor WHERE goal=%s AND principal=%s', (f.ids['goal'], actor['id'])),
            lambda: f.sql('INSERT INTO hb_cooperation_executor VALUES(%s,%s,%s)', (f.ids['authority'], f.ids['goal'], actor['id'])), 403)
        context = f.sql('SELECT context_id FROM hb_agent_context WHERE principal=%s', (actor['id'],))['context_id']
        await fault('context', lambda: f.sql('UPDATE hb_agent_context SET context_id=%s WHERE principal=%s', (digest('synthetic-different-context'), actor['id'])),
            lambda: f.sql('UPDATE hb_agent_context SET context_id=%s WHERE principal=%s', (context, actor['id'])))
        await fault('unaccepted-prerequisite', lambda: f.sql('INSERT INTO hb_work_dependency VALUES(%s,%s,%s,%s)',
            (f.ids['authority'], f.ids['goal'], work_id, f.agents['p']['work'])),
            lambda: f.sql('DELETE FROM hb_work_dependency WHERE work=%s', (work_id,)))
        await fault('work-change-during-apply', lambda: f.sql('UPDATE hb_work SET revision=revision+1 WHERE id=%s', (work_id,)),
            lambda: f.sql('UPDATE hb_work SET revision=2 WHERE id=%s', (work_id,)), after_begin=True)
        checks.append('Synthetic authority fault matrix rejects changed owner/revision/attempt/cooperation/context/prerequisites and hidden Work; original reservations/executing locks remain untouched')

        async def held_after(item, body, permit, status=409):
            await phase(item, body, permit, 'begin', status=status)
            view = await f.http(actor['host']['token'], 'GET', 'collaboration/delivery-edits/' + item['permitId'])
            assert view['effectiveState'] == 'held'
            assert f.sql('SELECT state FROM hb_edit_permit WHERE id=%s', (item['permitId'],))['state'] == 'reserved'
            assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s', (item['permitId'],))
        stale_goal = case('supported/goal-pause.txt')
        body, permit = await reserve(stale_goal)
        for state in ('paused', 'active'):
            await f.http(owner, 'POST', 'goals/' + f.ids['goal'] + '/state', {'operationId': uid(),
                'revision': f.sql('SELECT revision FROM hb_goal WHERE id=%s', (f.ids['goal'],))['revision'],
                'state': state, 'note': 'SYNTHETIC supported pause/resume; no executor stop'})
            await held_after(stale_goal, body, permit)
        stale_placement = case('supported/placement-pause.txt')
        body, permit = await reserve(stale_placement)
        for enabled in (False, True):
            await f.http(owner, 'PUT', 'placements/' + actor['id'], {'operationId': uid(),
                'revision': f.sql('SELECT revision FROM hb_placement WHERE agent=%s', (actor['id'],))['revision'],
                'hostId': actor['host']['id'], 'enabled': enabled})
            await held_after(stale_placement, body, permit)
        stale_membership = case('supported/membership.txt')
        body, permit = await reserve(stale_membership)
        for can_post in (False, True):
            await f.http(owner, 'PUT', 'rooms/' + f.ids['room'] + '/membership', {'operationId': uid(),
                'revision': f.sql('SELECT revision FROM hb_membership WHERE room=%s AND principal=%s',
                                 (f.ids['room'], actor['id']))['revision'], 'principalId': actor['id'],
                'can_read': True, 'can_post': can_post, 'can_request': True})
            await held_after(stale_membership, body, permit, 403 if not can_post else 409)
        stale_decision = case('supported/decision.txt')
        body, permit = await reserve(stale_decision)
        goal_source = f.sql('SELECT source FROM hb_goal WHERE id=%s', (f.ids['goal'],))['source']
        await f.http(owner, 'POST', 'rooms/' + f.ids['room'] + '/decisions', {'operationId': uid(), 'id': uid(),
            'revision': 0, 'key': 'work-direct-test', 'goalId': f.ids['goal'], 'state': 'active',
            'body': 'SYNTHETIC changed direction', 'sourceEventId': str(goal_source)})
        await held_after(stale_decision, body, permit)  # frame uses a NEW numeric watermark; pin still differs.
        checks.append('Supported Goal/placement pause-resume, room revoke-regrant and newer decisions cannot revive original Work permits, even with a newer numeric watermark')

        unknown_case = case('unknown/shared.txt')
        body, permit = await reserve(unknown_case)
        _, begun = await phase(unknown_case, body, permit, 'begin')
        _, unknown_result = await phase(unknown_case, body, begun['receipt']['permit'], 'settle', outcome='unknown')
        unknown = unknown_result['receipt']['permit']
        assert unknown['state'] == 'held'
        locks = f.sql('SELECT * FROM hb_edit_file_lock WHERE permit=%s', (unknown_case['permitId'],), many=True)
        await reserve({**unknown_case, 'permitId': uid()}, f.agents['p'], status=409)
        await reserve({**unknown_case, 'permitId': uid()}, status=409)
        assert f.sql('SELECT * FROM hb_edit_file_lock WHERE permit=%s', (unknown_case['permitId'],), many=True) == locks
        cancelled_case = case('supported/cancel.txt')
        cancel_body, cancel_permit = await reserve(cancelled_case, f.agents['p'])
        await f.http(owner, 'POST', 'work/' + f.agents['p']['work'] + '/cancel', {
            'operationId': uid(), 'revision': 2, 'note': 'SYNTHETIC supported cancellation; executor not claimed stopped'})
        await phase(cancelled_case, cancel_body, cancel_permit, 'begin', f.agents['p'], status=409)
        assert f.sql('SELECT state FROM hb_edit_permit WHERE id=%s', (cancelled_case['permitId'],))['state'] == 'reserved'
        assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s', (cancelled_case['permitId'],))
        checks.append('Unknown Work mutation holds the common namespace against same/peer direct edits; supported cancellation denies begin and preserves original lock')

        (f.out / 'work-authority-faults.json').write_text(json.dumps(faults, indent=2) + '\n')
        evidence = {'capabilities': capabilities, 'positiveAuthority': pin, 'ordinaryAuthority': baseline['proof']['workAuthority'],
            'unknownPermit': unknown, 'unknownLocks': locks, 'contextsBefore': contexts_before,
            'workBefore': work_before, 'workAfter': f.sql('SELECT * FROM hb_work ORDER BY id', many=True),
            'faults': faults, 'nativeProvenance': 'SYNTHETIC fixture only', 'originalNativeProofRepeated': False}
        (f.out / 'work-delivery-evidence.json').write_text(json.dumps(json_value(evidence), indent=2) + '\n')
        result = {'state': 'passed', 'checks': checks, 'httpCalls': len(f.trace), 'authorityFaults': len(faults),
            'nativeStarts': 0, 'controllerStarts': 0, 'modelStarts': 0, 'productionChanges': 0,
            'nativeProvenance': 'SYNTHETIC fixture only', 'nativeRuntimeProof': False,
            'fixtureRAMApplications': 2, 'originalNativeProofRepeated': False}
        (f.out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'state': 'passed', 'groups': len(checks), 'httpCalls': len(f.trace)}))
    finally:
        await f.close()


if __name__ == '__main__':
    asyncio.run(main())
