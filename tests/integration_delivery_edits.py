"""Bounded real PG/HTTP mechanism checks; all native provenance is SYNTHETIC.

Ordinary agents have no Goal/Work/plan rows. Existing explicit planned fixtures
are used only to qualify coexistence/conflict, never to make direct edits pass.
"""
import asyncio
import copy
import json
import secrets

from edit_plan_fixture import Fixture, uid, sha
from hollywood_backbone.physical_workspaces import paths_for
from hollywood_backbone.store import digest


async def main():
    f = Fixture()
    await f.start()
    ordinary = []
    checks = []
    initial_bridge = f.sql('SELECT * FROM hb_collaboration_bridge ORDER BY host', many=True)
    original_work = f.sql('SELECT * FROM hb_work ORDER BY id', many=True)
    original_attempts = f.sql('SELECT * FROM hb_work_attempt ORDER BY delivery', many=True)
    resource = f.resources[f.agents['i']['workspace']]

    async def grant(agent, state='active', status=200, body=None):
        old = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                    (resource['id'], agent['id']))
        body = body or {'operationId': uid(), 'revision': old['revision'] if old else 0,
            'workspaceRevision': resource['revision'], 'executionHost': agent['host']['id'],
            'state': state, 'canRead': True, 'canWrite': True}
        result = await f.http(f.credentials['Owner']['token'], 'PUT',
            f"physical-workspaces/{resource['id']}/agent-grants/{agent['id']}", body, status=status)
        return body, result

    async def new_agent(name, with_access=True):
        host = f.hosts['host1']
        agent = {'id': uid(), 'token': secrets.token_urlsafe(40), 'host': host, 'name': name,
                 'privateWorkspace': uid(), 'workspace': resource['id'], 'root': resource['root'], 'work': None}
        f.store.principal(f.owner, agent['id'], 'SYNTHETIC ordinary '+name, 'agent', agent['token'])
        f.store.membership(f.owner, f.ids['room'], agent['id'], {'can_read': True, 'can_post': True, 'can_request': True}, 0)
        f.store.placement(f.owner, host['id'], 'SYNTHETIC existing host', 'synthetic-identity-proof', {}, agent['id'])
        # Grant a newly placed principal before any native context or work exists.
        if with_access:
            body, result = await grant(agent)
            assert (await grant(agent, body=body))[1] == result
        else:
            assert not f.sql('SELECT 1 FROM hb_physical_agent_grant WHERE principal=%s', (agent['id'],))
        assert not f.sql('SELECT 1 FROM hb_agent_context WHERE principal=%s', (agent['id'],))
        assert not f.sql('SELECT 1 FROM hb_work WHERE owner=%s', (agent['id'],))
        request = f.store.post(f.owner, f.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': agent['id'], 'body': 'SYNTHETIC ordinary request; no business planning prerequisite.'})
        f.bind(agent, request['deliveries'][0]['id'], request['event']['id'])
        agent['source']['tool'] = 'apply_patch'
        f.agents[name] = agent
        ordinary.append(agent)
        return agent

    def observed(case):
        result = []
        for item in case['files']:
            path = resource['root']/item['relativePath']
            data = path.read_bytes() if path.exists() else None
            result.append({'relativePath': item['relativePath'], 'sha256': sha(data) if data is not None else 'absent',
                           'size': len(data) if data is not None else 0})
        return result

    def case(path, before=b'original\n', after=b'edited\n'):
        actual = resource['root']/path
        actual.parent.mkdir(parents=True, exist_ok=True)
        if before is not None:
            actual.write_bytes(before)
        return {'permitId': uid(), 'patchSha256': sha(('SYNTHETIC native patch:'+path).encode()),
                'files': [{'relativePath': path, 'beforeSha256': sha(before) if before is not None else 'absent',
                           'beforeSize': len(before) if before is not None else 0,
                           'afterSha256': sha(after), 'afterSize': len(after)}], 'after': after}

    def frame(agent, case, action='preflight', permit=None, source=None, **extra):
        command = {'action': action, 'permitId': case['permitId'], 'briefingSequence': 0,
                   'note': 'SYNTHETIC ordinary delivery qualification', 'sourceRevision': None,
                   'patchSha256': case['patchSha256'], 'filesObserved': observed(case)}
        if action == 'preflight':
            command.update(files=case['files'], leaseSeconds=30)
        else:
            command['permitRevision'] = permit['revision']
        command.update(extra)
        return {'schema': 'canonical-delivery-edits/v1', 'scope': 'delivery', 'operationId': uid(),
                'bridgeRevision': agent['host']['revision'], 'source': source or {**agent['source'], 'callId': uid()},
                'executorId': agent['executor'], 'descriptorDigest': 'd'*64, 'serviceBindingDigest': 'e'*64,
                'channel': 'captured-app-server-rpc/v1', 'command': command}

    def attest(body):
        actor_id = body.get('source', {}).get('agentId')
        if actor_id is None:
            actor_id = f.sql('SELECT source FROM hb_edit_permit WHERE id=%s', (body['permitId'],))['source']['agentId']
        access = f.sql('SELECT revision FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                       (resource['id'], actor_id))
        with f.store.pool.connection() as c:
            paths = sorted(set(paths_for(c, body)))
        for path in paths:
            current = resource['root']
            for part in path.split('/'):
                current /= part
                assert not current.is_symlink()
            assert current.resolve().is_relative_to(resource['root'].resolve())
        body['target'] = {'schema': 'hollywood-physical-target/v1', 'workspaceId': resource['id'],
            'workspaceRevision': resource['revision'], 'grantRevision': access['revision'] if access else 1,
            'environmentSha256': resource['environment'], 'rootIdentitySha256': resource['identity'],
            'resolution': 'beneath-no-symlinks/v1', 'actionDigest': digest({k:v for k,v in body.items() if k!='target'}),
            'paths': [{'relativePath': path, 'resolvedPath': str(resource['root']/path)} for path in paths]}
        return body

    async def call(agent, body, status=200, token=None, resolve=True, path='collaboration/delivery-edits'):
        if resolve:
            attest(body)
        return await f.http(token or agent['host']['token'], 'POST', path, body, status=status, attest=False)

    async def reserve(agent, case):
        body = frame(agent, case)
        result = await call(agent, body)
        assert result['admission'] is None and result['receipt']['permit']['plan'] is None
        assert result['receipt']['permit']['scope_kind'] == 'delivery'
        return body, result['receipt']['permit']

    async def phase(agent, case, original, permit, action, **extra):
        body = frame(agent, case, action, permit, copy.deepcopy(original['source']), **extra)
        return body, await call(agent, body)

    try:
        a, b = await new_agent('ordinary-a'), await new_agent('ordinary-b')
        denied = await new_agent('ordinary-no-grant', with_access=False)
        denied_case = case('ordinary/no-grant.txt')
        await call(denied, frame(denied, denied_case), 403)
        assert not f.sql('SELECT 1 FROM hb_edit_permit WHERE id=%s', (denied_case['permitId'],))
        assert initial_bridge == f.sql('SELECT * FROM hb_collaboration_bridge ORDER BY host', many=True)
        checks.append('New enrolled principals receive canonical admin access before context/work, with identical bridge enrollment; absent grant defaults deny.')
        target_read = await f.http(a['host']['token'], 'GET', 'collaboration/delivery-targets/'+a['source']['deliveryId'])
        assert target_read['principalId'] == a['id'] and target_read['targets'][0]['grant_revision'] == 1
        positive = case('ordinary/success.txt')
        original, permit = await reserve(a, positive)
        begin_body, begun = await phase(a, positive, original, permit, 'begin')
        assert begun['admission']['mayApply'] and begun['admission']['oneShotOriginalResponseOnly']
        replay = await call(a, begin_body)
        assert replay['admission'] is None and replay['replayed'] and replay['receipt'] == begun['receipt']
        altered = copy.deepcopy(begin_body);altered['command']['note'] = 'changed same operation'
        await call(a, altered, 409)
        counter = 0
        if begun['admission']['mayApply']:
            (resource['root']/positive['files'][0]['relativePath']).write_bytes(positive['after']);counter += 1
        _, settled = await phase(a, positive, original, begun['receipt']['permit'], 'settle', outcome='applied')
        assert counter == 1 and settled['receipt']['permit']['state'] == 'settled'
        assert not f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s', (positive['permitId'],))
        receipt = await f.http(a['host']['token'], 'GET', f"collaboration/commands/{a['id']}/{begin_body['operationId']}")
        assert receipt['acceptedResult']['mayApply'] is False and receipt['receiptOnly']
        checks.append('Ordinary non-Git native-shaped preflight/begin/applied settlement; one original apply grant, replay/changed-payload refusal and exact original receipt recovery; effect counter=1.')

        # Scope/provenance and current canonical fences, without changing old work.
        guarded = case('ordinary/guarded.txt')
        body = frame(a, guarded)
        await call(a, body, 403, token=a['token'])
        wrong = frame(a, guarded);attest(wrong);wrong['target']['environmentSha256'] = 'f'*64
        await call(a, wrong, 409, resolve=False)
        wrong = frame(a, guarded);wrong['descriptorDigest'] = 'f'*64
        await call(a, wrong, 409)
        f.sql('UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s', (a['source']['deliveryId'],))
        await call(a, frame(a, guarded), 409)
        f.sql('UPDATE hb_delivery SET cancellation_requested=false WHERE id=%s', (a['source']['deliveryId'],))
        f.sql('UPDATE hb_agent_role SET revision=revision+1 WHERE principal=%s', (a['id'],))
        await call(a, frame(a, guarded), 409)
        f.sql('UPDATE hb_agent_role SET revision=revision-1 WHERE principal=%s', (a['id'],))
        structured = frame(f.agents['i'], guarded);structured['source']['tool'] = 'apply_patch'
        await call(f.agents['i'], structured, 403)  # No ordinary physical-agent grant.
        checks.append('Agent bearer cannot relay protected mutation; wrong environment/executor descriptor, cancellation, changed role and structured-work escape refuse without mutation.')

        # Both ordinary actors have different private contexts but the SAME root.
        race_case = case('ordinary/race.txt')
        bodies = [attest(frame(agent, race_case | {'permitId': uid()})) for agent in (a,b)]
        async def race(agent, body):
            async with f.client.post(f.url+'collaboration/delivery-edits', json=body,
                                     headers={'Authorization':'Bearer '+agent['host']['token']}) as response:
                result = await response.json()
                row = {'status': response.status, 'request': body, 'result': result}
                with (f.out/'delivery-race.jsonl').open('a') as out:out.write(json.dumps(row)+'\n')
                return response.status,result
        results = await asyncio.gather(*(race(agent, body) for agent,body in zip((a,b),bodies)))
        assert sorted(x[0] for x in results) == [200,409]
        winner = next(index for index,x in enumerate(results) if x[0]==200)
        agent, body = (a,b)[winner], bodies[winner]
        raced = {**race_case,'permitId':body['command']['permitId']}
        _, begun = await phase(agent, raced, body, results[winner][1]['receipt']['permit'], 'begin')
        _, settled = await phase(agent, raced, body, begun['receipt']['permit'], 'settle', outcome='notApplied')
        assert settled['receipt']['permit']['state']=='settled'
        checks.append('Concurrent ordinary actors with distinct private contexts share one physical all-file lock; exactly one preflight succeeds. Conclusive notApplied releases only its original locks.')

        # Existing planned admission remains explicit, including real claim rows.
        planned = await f.case('planned-coexistence')
        await f.propose(planned);await f.ready(planned)
        plan_body, planned_response = await f.reserve(planned)
        conflict = {'permitId':uid(), 'patchSha256':'f'*64,
                    'files':[{k:v for k,v in planned['manifest']['files'][0].items() if k not in ('claimId','claimRevision')}]}
        await call(a, frame(a, conflict), 409)
        plan_begin = f.phase(planned, plan_body, planned_response['receipt']['permit'], 'begin')
        plan_begun = await f.call(planned['integrator'], body=plan_begin)
        assert plan_begun['admission']['mayApply']
        f.apply_bytes(planned)
        plan_settle = f.phase(planned, plan_body, plan_begun['receipt']['permit'], 'settle', outcome='applied')
        assert (await f.call(planned['integrator'], body=plan_settle))['receipt']['permit']['state']=='settled'
        checks.append('Existing four-party planned consent/preflight/begin/settle still passes; direct scope cannot override its canonical ownership.')

        held_case = case('ordinary-lock/leaf.txt')
        held_origin, held = await reserve(a, held_case)
        claimant = f.agents['i']
        claim = f.envelope(claimant, {'action':'acquire', 'workId':claimant['work'], 'workRevision':2,
            'claimId':uid(),'claimRevision':0,'briefingSequence':0,'note':'SYNTHETIC cross-mode lock',
            'relativePath':'ordinary-lock','kind':'directory','sourceRevision':'a'*40,'contentSha256':'absent','leaseSeconds':60})
        claim['source']['tool']='coordination_act'
        await f.http(claimant['host']['token'],'POST','collaboration/path-claims',claim,status=409)
        claim['operationId']=uid();claim['command']['claimId']=uid();claim['command']['relativePath']='legacy-owned'
        acquired = await f.http(claimant['host']['token'],'POST','collaboration/path-claims',claim)
        blocked = case('legacy-owned/leaf.txt')
        f.sql("UPDATE hb_path_claim SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(acquired['claim']['id'],))
        await call(a,frame(a,blocked),409)
        checks.append('Direct locks block legacy directory acquisition; unreleased expired directory claims block ordinary descendants. Shared advisory/physical lock domain is retained.')

        _, begun = await phase(a,held_case,held_origin,held,'begin')
        _, unknown = await phase(a,held_case,held_origin,begun['receipt']['permit'],'settle',outcome='unknown')
        assert unknown['receipt']['permit']['state']=='held'
        assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s',(held_case['permitId'],))
        await call(b,frame(b,held_case|{'permitId':uid()}),409)
        revoked = case('ordinary/revoked.txt')
        revoke_origin, revoke_permit = await reserve(b,revoked)
        await grant(b,'revoked')
        await call(b,frame(b,revoked,'begin',revoke_permit,revoke_origin['source']),403)
        state = await f.http(b['host']['token'],'GET','collaboration/delivery-edits/'+revoked['permitId'])
        assert state['effectiveState']=='held' and state['mayApply'] is False
        assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s',(revoked['permitId'],))
        checks.append('Unknown effects and revoked resource access retain exact original locks; readback/replay never grants another application or expires ownership.')

        # Controller observation after the original ordinary executor stops is
        # evidence only. It does not fabricate a later model/review lifecycle.
        f.sql("UPDATE hb_delivery SET state='completed' WHERE id=%s",(a['source']['deliveryId'],))
        f.sql("UPDATE hb_executor SET state='stopped',observed_at=clock_timestamp(),evidence=jsonb_set(evidence,'{reason}','\"registered-unit-drained\"') WHERE id=%s",(a['executor'],))
        observation={'operationId':uid(),'bridgeRevision':a['host']['revision'],'permitId':held_case['permitId'],
            'permitRevision':unknown['receipt']['permit']['revision'],'executorId':a['executor'],
            'descriptorDigest':'d'*64,'serviceBindingDigest':'e'*64,'channel':'stopped-executor-observation/v1',
            'outcome':'notApplied','filesObserved':observed(held_case),'note':'SYNTHETIC independently stopped observation'}
        evidence=await call(a,observation,path='collaboration/mutation-observations')
        assert evidence['receipt']['permit']['state']=='held' and evidence['admission'] is None
        assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s',(held_case['permitId'],))
        # Review is a distinct authenticated action. Observation never unlocks.
        held = evidence['receipt']['permit']
        review = {'operationId':uid(), 'permitRevision':held['revision'],
                  'observationDigest':evidence['receipt']['observationDigest'],
                  'outcome':'notApplied', 'effectDisposition':'reviewed',
                  'note':'SYNTHETIC explicit admin review of stopped original before-content'}
        route = 'collaboration/delivery-edits/'+held_case['permitId']+'/reconcile'
        await f.http(b['token'],'POST',route,review,status=403)
        wrong = {**review,'observationDigest':'0'*64}
        await f.http(f.credentials['Owner']['token'],'POST',route,wrong,status=409)
        f.sql("UPDATE hb_executor SET state='live' WHERE id=%s",(a['executor'],))
        await f.http(f.credentials['Owner']['token'],'POST',route,review,status=409)
        f.sql("UPDATE hb_executor SET state='stopped' WHERE id=%s",(a['executor'],))
        assert f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s',(held_case['permitId'],))
        accepted = await f.http(f.credentials['Owner']['token'],'POST',route,review)
        assert accepted['receipt']['permit']['state']=='settled'
        observation_event = f.sql('SELECT data FROM hb_event WHERE id=%s',
                                  (evidence['receipt']['eventId'],))
        review_event = f.sql('SELECT data FROM hb_event WHERE id=%s',
                             (accepted['receipt']['eventId'],))
        assert observation_event['data']['evidenceOnly'] is True
        assert review_event['data']['evidenceOnly'] is False
        assert accepted['admission'] is None and accepted['receipt']['mayApply'] is False
        assert accepted['receipt']['permit']['settlement']['originalSettlement']['outcome']=='unknown'
        assert not f.sql('SELECT 1 FROM hb_edit_file_lock WHERE permit=%s',(held_case['permitId'],))
        replay = await f.http(f.credentials['Owner']['token'],'POST',route,review)
        assert replay == accepted
        await f.http(f.credentials['Owner']['token'],'POST',route,{**review,'note':'changed'},status=409)
        readback = await f.http(f.credentials['Owner']['token'],'GET',
                               'collaboration/delivery-edits/'+held_case['permitId'])
        assert readback['permit']['settlement']['effectDisposition']=='reviewed'
        assert readback['mayApply'] is False and not readback['newExecutionAdmitted']
        checks.append('Explicit administrator review releases only exact stopped original observed scope; unauthorized, changed observation/live executor and altered replay refuse; original unknown settlement and audit remain; no new execution admission.')
        chat=await f.http(f.credentials['Owner']['token'],'GET',f"rooms/{f.ids['room']}/history?view=conversation&limit=100")
        assert not any(e['data'].get('type')=='collaborationProtocol' for e in chat['events'])
        assert original_work==f.sql('SELECT * FROM hb_work ORDER BY id',many=True)
        assert original_attempts==f.sql('SELECT * FROM hb_work_attempt ORDER BY delivery',many=True)
        assert all(not f.sql('SELECT 1 FROM hb_work WHERE owner=%s',(x['id'],)) for x in ordinary)
        assert initial_bridge==f.sql('SELECT * FROM hb_collaboration_bridge ORDER BY host',many=True)
        checks.append('Stopped original observations remain held evidence, protocol audit stays out of conversation, and all original work/attempt rows and bridge enrollment remain unchanged. Ordinary actors have zero work rows.')
        (f.out/'delivery-edit-report.json').write_text(json.dumps({'schema':'bounded-delivery-edit-qualification/v1',
            'checks':checks,'nativeSource':'SYNTHETIC','nativeModelsStarted':0,'ordinaryWorkRows':0,
            'positiveEffectCounter':counter,'realPostgreSQL':True,'realAuthenticatedHttp':True},indent=2)+'\n')
        print(json.dumps({'checks':len(checks),'state':'passed','nativeProof':False,'ordinaryWorkRows':0}))
    finally:
        await f.close()


if __name__=='__main__':
    asyncio.run(main())
