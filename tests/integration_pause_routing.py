"""Actual PostgreSQL/HTTP regression from the native cooperative edit journey.

Exercise queued input and stable workspace routing across a maintenance pause.
No model, native executor or existing service is started or altered.
"""
import asyncio
import json
from integration_resource_routing import DirectoryFixture
from edit_plan_fixture import uid

async def main():
    f = DirectoryFixture()
    await f.start()
    agent, host = f.agents['i'], f.hosts['host1']
    owner = f.credentials['Owner']['token']
    pointer = f"agents/{agent['id']}/resource-defaults/{host['id']}"
    placement = 'placements/' + agent['id']
    try:
        workspace = uid()
        await f.register_root(workspace, host['id'], f.out / 'resource' / workspace)
        root = f.sql('SELECT revision FROM hb_physical_workspace WHERE id=%s', (workspace,))
        await f.http(owner, 'PUT', f"physical-workspaces/{workspace}/agent-grants/{agent['id']}", {
            'operationId': uid(), 'revision': 0, 'workspaceRevision': root['revision'],
            'executionHost': host['id'], 'state': 'active', 'canRead': True, 'canWrite': True})
        async def select(revision):
            grant = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s', (workspace, agent['id']))
            return await f.http(owner, 'PUT', pointer, {
                'operationId': uid(), 'revision': revision, 'state': 'selected',
                'workspaceId': workspace, 'workspaceRevision': root['revision'], 'grantRevision': grant['revision']})
        async def change(enabled):
            current = await f.http(owner, 'GET', placement)
            return await f.http(owner, 'PUT', placement, {
                'operationId': uid(), 'revision': current['placement']['revision'],
                'hostId': host['id'], 'enabled': enabled})
        selected = await select(0)
        assert selected['effectiveState'] == 'selected'
        original = selected['selection']
        queued = await f.http(owner, 'POST', 'rooms/' + f.ids['room'] + '/messages', {
            'operationId': uid(), 'recipientId': agent['id'], 'kind': 'request',
            'audience': 'room', 'body': 'Disposable pause-routing queued input; no executor is enrolled.'})
        await change(False)
        paused = await f.http(owner, 'GET', pointer)
        assert paused['effectiveState'] == 'held' and paused['holdReason'] == 'participation-unavailable'
        assert paused['selection'] == original
        assert not f.sql('SELECT 1 FROM hb_resource_default_hold WHERE principal=%s', (agent['id'],))
        await f.http(agent['token'], 'POST', 'connector/claim', {
            'operationId': uid(), 'hostId': host['id'], 'capabilities': []}, status=403)
        await change(True)
        resumed = await f.http(owner, 'GET', pointer)
        assert resumed['effectiveState'] == 'selected' and resumed['selection'] == original
        pending = await f.http(agent['token'], 'GET', 'connector/pending?hostId=' + host['id'])
        assert pending['pending']['id'] == queued['deliveries'][0]['id'] and pending['busy'] is None
        # Real authority revocation still leaves permanent historical evidence.
        f.sql('UPDATE hb_principal SET active=false WHERE id=%s', (agent['id'],))
        f.sql('UPDATE hb_principal SET active=true WHERE id=%s', (agent['id'],))
        revoked = await f.http(owner, 'GET', pointer)
        assert revoked['effectiveState'] == 'held' and revoked['holdReason'] == 'participation-invalidated'
        holds = f.sql('SELECT * FROM hb_resource_default_hold WHERE principal=%s', (agent['id'],), many=True)
        f.store.migrate()
        assert f.sql('SELECT * FROM hb_resource_default_hold WHERE principal=%s', (agent['id'],), many=True) == holds
        renewed = await select(1)
        assert renewed['selectionRevision'] == 2 and renewed['effectiveState'] == 'selected'
        await change(False)
        await change(True)
        final = await f.http(owner, 'GET', pointer)
        assert final['effectiveState'] == 'selected' and final['selectionRevision'] == 2
        result = {'state': 'passed', 'schema': f.sql('SELECT max(version) AS version FROM hb_schema')['version'],
                  'sameRouteAcrossPause': True, 'pausedClaimDenied': True, 'queuedInputPreserved': True,
                  'realRevocationRemainsHeld': True, 'existingHoldsPreservedByMigration': True,
                  'explicitReselectionWorks': True, 'modelsStarted': 0, 'existingServicesChanged': False}
        (f.out / 'pause-routing-result.json').write_text(json.dumps(result, indent=2) + chr(10))
        print(json.dumps(result))
    finally:
        await f.close()

if __name__ == '__main__':
    asyncio.run(main())
