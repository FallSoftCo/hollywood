"""Actual RAM/SQL/HTTP resource and projection checks; native facts SYNTHETIC."""
import copy
import json
from pathlib import Path
from edit_plan_fixture import uid, sha
from hollywood_backbone.store import digest


async def qualify(f):
    groups = []
    i, p, t, remote = (f.agents[n] for n in ('i', 'p', 't', 'r'))
    owner = f.credentials['Owner']['token']
    assert i['workspace'] == p['workspace'] and i['privateWorkspace'] != p['privateWorkspace']
    assert len({a['privateWorkspace'] for a in f.agents.values()}) == 5
    original = f.sql('SELECT * FROM hb_agent_context ORDER BY principal', many=True)

    def claim(agent, path='physical/shared.txt'):
        command = {'action': 'acquire', 'workId': agent['work'], 'workRevision': 2, 'claimId': uid(),
            'claimRevision': 0, 'briefingSequence': 0, 'note': 'SYNTHETIC registered resource test',
            'relativePath': path, 'kind': 'file', 'sourceRevision': 'a' * 40,
            'contentSha256': sha(b'resource bytes'), 'leaseSeconds': 300}
        body = f.envelope(agent, command)
        body['source']['tool'] = 'coordination_act'
        return body

    actual = i['root'] / 'physical/shared.txt'
    actual.parent.mkdir()
    actual.write_bytes(b'resource bytes')
    body = claim(i)
    receipt = await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', body)
    assert receipt['claim']['workspace_id'] == i['workspace']
    assert receipt['claim']['workspace_id'] != i['privateWorkspace']
    await f.http(p['host']['token'], 'POST', 'collaboration/path-claims', claim(p), 409)
    groups.append('Distinct private contexts share registered physical ownership; same-checkout overlap is denied without changing either context')

    def transfer(agent, action, revision, **extra):
        item = f.envelope(agent, {'action': action, 'workId': agent['work'], 'workRevision': 2,
            'claimId': receipt['claim']['id'], 'claimRevision': revision, 'briefingSequence': 0,
            'note': 'SYNTHETIC explicit shared-resource handoff', **extra})
        item['source']['tool'] = 'coordination_act'
        return item
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', transfer(i, 'handoff', 1,
        targetWorkId=t['work'], targetWorkRevision=2, effectDisposition='reviewed'), 403)
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', transfer(i, 'handoff', 1,
        targetWorkId=p['work'], targetWorkRevision=2, effectDisposition='reviewed'))
    accepted = await f.http(p['host']['token'], 'POST', 'collaboration/path-claims',
        transfer(p, 'accept', 2, leaseSeconds=300))
    assert accepted['claim']['owner'] == p['id'] and accepted['claim']['workspace_id'] == i['workspace']
    groups.append('Explicit offer/accept across distinct private contexts preserves shared physical namespace; an ungranted recipient cannot receive it')
    f.sql('UPDATE hb_path_claim SET target=NULL WHERE id=%s', (receipt['claim']['id'],))
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', claim(i, 'physical/legacy-held.txt'), 409)
    released = await f.http(p['host']['token'], 'POST', 'collaboration/path-claims',
        transfer(p, 'release', 3, effectDisposition='reviewed'))
    assert released['claim']['state'] == 'released' and released['claim']['target'] is None
    groups.append('Legacy unregistered ownership blocks new target effects until explicit registered, reviewed cleanup; no automatic namespace conversion or receipt rewrite')

    extra = uid()
    await f.register_root(extra, i['host']['id'], f.out / 'extra-checkout')
    await f.grant_root(i, extra)
    second = f.attest(claim(i), workspace=extra)
    result = await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', second, attest=False)
    assert result['claim']['workspace_id'] == extra
    await f.http(t['host']['token'], 'POST', 'collaboration/path-claims', claim(t))
    await f.grant_root(i, remote['workspace'])
    routed = f.attest(claim(i), workspace=remote['workspace'])
    result = await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', routed, attest=False)
    assert result['claim']['host'] == remote['host']['id'] != i['source']['hostId']
    assert result['source']['hostId'] == i['source']['hostId']
    targets = await f.http(i['host']['token'], 'GET', 'collaboration/physical-targets/' + i['work'])
    assert len(targets['targets']) == 3 and targets['mayApply'] is False
    groups.append('Multiple granted roots and routed physical host/environment remain distinct from source host/private workspace, with no false same-path collision')

    good = f.attest(claim(i, 'physical/denied.txt'))
    await f.http(i['token'], 'POST', 'collaboration/path-claims', good, 403, attest=False)
    missing = copy.deepcopy(good)
    missing.pop('target')
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', missing, 400, attest=False)
    unregistered = copy.deepcopy(good)
    unregistered['target']['workspaceId'] = uid()
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', unregistered, 403, attest=False)
    unauthorized = f.attest(claim(i), workspace=t['workspace'])
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', unauthorized, 403, attest=False)
    for field in ('environmentSha256', 'rootIdentitySha256', 'actionDigest'):
        changed = copy.deepcopy(good)
        changed['target'][field] = 'f' * 64
        await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', changed, 409, attest=False)
    escaped = copy.deepcopy(good)
    escaped['target']['paths'][0]['resolvedPath'] = str(f.out / 'outside.txt')
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', escaped, 403, attest=False)
    injected = copy.deepcopy(good)
    injected['command']['workspaceId'] = extra
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', injected, 400, attest=False)
    outside = f.out / 'outside'
    outside.mkdir()
    (outside / 'untouched.txt').write_bytes(b'unchanged')
    (i['root'] / 'alias').symlink_to(outside, target_is_directory=True)
    try:
        f.attest(claim(i, 'alias/untouched.txt'))
    except AssertionError as error:
        assert 'symlink' in str(error)
    else:
        raise AssertionError('Fixture protected resolver accepted symlink escape')
    assert (outside / 'untouched.txt').read_bytes() == b'unchanged'
    resource = f.resources[i['workspace']]
    registration = {'operationId': uid(), 'revision': 0, 'state': 'active',
        'rootPath': str(resource['root']), 'environmentSha256': resource['environment'],
        'rootIdentitySha256': resource['identity'], 'registrationSha256': 'c' * 64}
    await f.http(owner, 'PUT', f"hosts/{i['host']['id']}/physical-workspaces/{uid()}", registration, 409)
    registration['rootPath'] = str(resource['root'] / 'nested')
    registration['rootIdentitySha256'] = 'f' * 64
    await f.http(owner, 'PUT', f"hosts/{i['host']['id']}/physical-workspaces/{uid()}", registration, 409)
    f.sql("UPDATE hb_collaboration_bridge SET protocol='canonical-edit-plans/v1' WHERE host=%s", (i['host']['id'],))
    await f.http(i['host']['token'], 'POST', 'collaboration/path-claims', good, 403, attest=False)
    f.sql("UPDATE hb_collaboration_bridge SET protocol='canonical-edit-plans/v2' WHERE host=%s", (i['host']['id'],))
    groups.append('Unregistered/ungranted/model-injected/aliased targets, wrong environment/root/action digest, legacy profile and worker bearer fail closed; actual RAM symlink escape rejected before forwarding')

    # Intentionally retain legacy-shaped events and compare exact original bytes
    # after read projection. New protocol receipt already exists from the claim.
    with f.store.pool.connection() as c:
        actor = f.store.actor(c, i['id'])
        historical = f.store.event(c, actor, f.ids['room'], 'progress', 'SYNTHETIC retained legacy bookkeeping',
            source=i['source']['requestId'], data={'collaborationReceipt': receipt})
        historical_plan = f.store.event(c, actor, f.ids['room'], 'progress', 'SYNTHETIC retained plan bookkeeping',
            source=i['source']['requestId'], data={'editPlanReceipt': {'mayApply': False}})
    before = f.sql('SELECT * FROM hb_event WHERE id=ANY(%s::uuid[]) ORDER BY seq',
                   ([str(historical), str(historical_plan), receipt['eventId']],), many=True)
    message = await f.http(owner, 'POST', f"rooms/{f.ids['room']}/messages", {
        'operationId': uid(), 'kind': 'message', 'audience': 'room', 'body': 'SYNTHETIC intentional collaboration message'})
    source = body['source']
    activity = {'claimId': source['claimId'], 'generation': source['generation'], 'nativeThread': source['nativeThread'],
        'nativeTurn': source['nativeTurn'], 'itemId': source['callId'], 'phase': 'started',
        'nativeItemDigest': digest(['SYNTHETIC original native started item', source['callId']]),
        'item': {'nativeType': 'functionCall', 'title': 'coordination_act', 'status': 'inProgress'}}
    await f.http(i['token'], 'POST', f"connector/deliveries/{source['deliveryId']}/activity", activity)
    conversation = await f.http(owner, 'GET', f"rooms/{f.ids['room']}/history?view=conversation&limit=100")
    rows = conversation['events']
    assert any(e['id'] == message['event']['id'] for e in rows)
    assert not any(e['data'].get('type') in ('executionActivity', 'collaborationProtocol')
        or 'collaborationReceipt' in e['data'] or 'editPlanReceipt' in e['data'] for e in rows)
    actions = await f.http(owner, 'GET', f"rooms/{f.ids['room']}/history?view=activity&limit=100")
    assert len(actions['events']) == 1
    data = actions['events'][0]['data']
    assert data['phase'] == 'started' and data['status'] == 'inProgress'
    assert len(data['protocolReceipts']) == 1
    link = data['protocolReceipts'][0]
    assert link['eventId'] == receipt['eventId'] and link['operationId'] == body['operationId']
    assert link['actorId'] == i['id'] and link['source'] == source and link['receipt'] == receipt
    audit = await f.http(owner, 'GET', f"rooms/{f.ids['room']}/history?view=all&limit=100")
    projected = next(e for e in audit['events'] if e['id'] == receipt['eventId'])
    assert projected['projection'] == {'kind': 'protocolAudit', 'conversation': False, 'actionCard': False,
        'eventId': receipt['eventId'], 'actorId': i['id'], 'operationId': body['operationId'],
        'source': source, 'receiptOnly': True}
    assert all(e['projection']['kind'] == 'protocolAudit' for e in audit['events']
        if e['id'] in (str(historical), str(historical_plan)))
    assert f.sql('SELECT * FROM hb_event WHERE id=ANY(%s::uuid[]) ORDER BY seq',
        ([str(historical), str(historical_plan), receipt['eventId']],), many=True) == before
    assert f.sql('SELECT count(*) AS n FROM hb_activity')['n'] == 1
    assert f.sql('SELECT * FROM hb_agent_context ORDER BY principal', many=True) == original
    groups.append('Intentional communication remains conversation; old/new protocol receipts stay immutable audit, linked to one supplied SYNTHETIC native started item with no extra lifecycle/card/completion')
    return groups
