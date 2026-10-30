"""Independent PostgreSQL stores, explicit home ownership and real NATS.

Synthetic identities only. No native executor, account or model is provisioned.
"""
import asyncio
import json
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from aiohttp import ClientSession, web
from hollywood_backbone.store import Store, Denied, Conflict, json_value, digest
from hollywood_backbone.server import application
from hollywood_backbone import native_effects
from hollywood_backbone.federation_identity import describe, install_reference, resolve_in_transaction
from hollywood_backbone.federation_service import Service, Client
from hollywood_backbone.federation_domain import AgentDomain
from hollywood_backbone.federation_host import HostDomain
from hollywood_backbone.backplane_auth import AgentBindings
from hollywood_backbone.federation_agent_gateway import LocalAgentGateway
from hollywood_backbone.federation_node_router import NodeRouter
from hollywood_backbone.backplane import BackplaneError
from integration_backplane import qualify


def expect_denied(call):
    try:call()
    except Denied:return
    raise AssertionError('Expected identity/audience denial')


async def main(database,binary,out):
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    source=json.loads(Path(database).read_text());authority=str(uuid4())
    stores=[];schemas=[];plans=[]
    try:
        for idx in range(2):
            schema='qa_node_home_'+uuid4().hex;schemas.append(schema)
            with psycopg.connect(**source,autocommit=True) as c:
                c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            cfg={**source,'options':source.get('options','')+' -c search_path='+schema}
            store=Store(cfg);stores.append(store);store.migrate()
            plan={'globalRoomId':str(uuid4()),'credentials':{name:{'principalId':str(uuid4()),
                'token':secrets.token_urlsafe(40)} for name in ('Owner','Global')}}
            plans.append(plan);store.bootstrap(authority,'Same organization, independent node '+str(idx),plan)
        def configure(a,b):
            async def check():
                services=[];runners=[]
                owner_a=plans[0]['credentials']['Owner']['principalId']
                owner_b=plans[1]['credentials']['Owner']['principalId']
                agent=plans[1]['credentials']['Global']['principalId'];token=plans[1]['credentials']['Global']['token']
                try:
                    for idx,t in enumerate((a,b)):
                        with stores[idx].pool.connection() as c:
                            c.execute('UPDATE hb_principal SET home_node=%s',(t.host_id,))
                            c.execute('UPDATE hb_room SET home_node=%s',(t.host_id,))
                            c.execute('INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s)',
                                (b.host_id,authority,'Explicit GPU QA host','fixture-only','{}'))
                    with stores[1].pool.connection() as c:
                        c.execute('INSERT INTO hb_placement(authority,agent,host) VALUES(%s,%s,%s)',
                            (authority,agent,b.host_id))
                    with stores[1].pool.connection() as c:
                        c.execute('UPDATE hb_principal SET administrator=false WHERE id=%s',(agent,))
                        c.execute("UPDATE hb_agent_role SET primary_role='worker' WHERE principal=%s",(agent,))
                    # Bootstrap Global relies on administrator access, not a
                    # membership. This fixture deliberately demotes it to a
                    # worker; grant its local room explicitly, as for any worker.
                    stores[1].membership(owner_b,plans[1]['globalRoomId'],agent,
                        {'can_read':True,'can_post':True,'can_request':True},0)
                    local_binding=AgentBindings(stores[1]);local_binding.migrate()
                    local_binding.bind(owner_b,host_id=b.host_id,principal_id=agent,token_digest=digest(token))
                    descriptor=describe(stores[1],owner_b,agent)
                    reference=install_reference(stores[0],owner_a,descriptor,enroll_execution_reference=True)
                    repeated=install_reference(stores[0],owner_a,descriptor,enroll_execution_reference=True)
                    assert reference==repeated and reference['grantRevision']==1
                    stores[0].membership(owner_a,plans[0]['globalRoomId'],agent,
                        {'can_read':True,'can_post':True,'can_request':True},0)
                    # References keep real display names without becoming another
                    # local owner or requiring artificial name/actor aliases.
                    assert stores[1].authenticate(token)['id']==agent
                    expect_denied(lambda:stores[0].authenticate(token))
                    expect_denied(lambda:stores[0].catalog(agent))
                    with stores[0].pool.connection() as c:
                        assert c.execute('SELECT count(*) n FROM hb_token WHERE principal=%s AND NOT revoked',(agent,)).fetchone()['n']==0
                    stores[0].post(owner_a,plans[0]['globalRoomId'],'synthetic-public',
                        {'kind':'message','body':'SYNTHETIC public room evidence','audience':'room'})
                    stores[0].post(owner_a,plans[0]['globalRoomId'],'synthetic-private',
                        {'kind':'message','body':'SYNTHETIC private room evidence','audience':'direct',
                         'recipientId':plans[0]['credentials']['Global']['principalId']})
                    async def read(request):
                        try:
                            p=request.payload
                            if set(p)!={'principalId','credentialDigest','homeNodeId'}:raise ValueError('Invalid identity read')
                            with stores[0].pool.connection() as c:
                                bound=resolve_in_transaction(c,authority_id=request.authority_id,
                                    source_host=request.source_host_id,principal_id=p['principalId'],
                                    home_node=p['homeNodeId'],credential_digest=p['credentialDigest'])
                            with native_effects.transport(p['principalId'],p['credentialDigest'],
                                    'GET','history','rooms/'+plans[0]['globalRoomId']+'/history',None,None,admission=bound):
                                return stores[0].history(p['principalId'],plans[0]['globalRoomId'],0,100)
                        except Denied:raise BackplaneError('AGENT_ACCESS_DENIED') from None
                    controller=str(uuid4());controller_token=secrets.token_urlsafe(40)
                    stores[0].principal(owner_a,controller,'QA original host controller','service',controller_token)
                    stores[0].host_policy_set(owner_a,b.host_id,str(uuid4()),{
                        'revision':0,'controllerId':controller,'machineIdentity':'a'*64,
                        'maxConcurrent':1,'reservedInteractive':0,'controlAgentIds':[],
                        'memoryPerTurnBytes':2147483648,'reserveMemoryBytes':4294967296,
                        'sampleMaxAgeSeconds':15,'connectorMaxAgeSeconds':30})
                    host_a=HostDomain(stores[0]);host_b=HostDomain(stores[1])
                    service=Service(a.connection,authority_id=authority,host_id=a.host_id,
                        node_id=a.host_id,legacy=False,handlers={'identity.read':read,
                        'hollywood.agent.authenticate':AgentDomain(AgentBindings(stores[0])).authenticate,
                        'hollywood.agent.domain':AgentDomain(AgentBindings(stores[0])).invoke,
                        'hollywood.host.authenticate':host_a.authenticate,'hollywood.host.domain':host_a.invoke})
                    await service.start();services.append(service)
                    domain_b=AgentDomain(local_binding)
                    home_service=Service(b.connection,authority_id=authority,host_id=b.host_id,
                        node_id=b.host_id,legacy=False,handlers={'hollywood.agent.authenticate':domain_b.authenticate,
                        'hollywood.agent.domain':domain_b.invoke,
                        'hollywood.host.authenticate':host_b.authenticate,'hollywood.host.domain':host_b.invoke})
                    await home_service.start();services.append(home_service)
                    caller=Client(b.connection,authority_id=authority,host_id=b.host_id,target_node_id=a.host_id)
                    payload={'principalId':agent,'credentialDigest':digest(token),'homeNodeId':b.host_id}
                    domain={'actorId':agent,'credentialDigest':digest(token),'homeNodeId':b.host_id,
                        'homeRevision':descriptor['principal']['home_revision'],
                        'homeRoleRevision':descriptor['role']['revision'],
                        'action':'history','verb':'GET','path':'rooms/'+plans[0]['globalRoomId']+'/history',
                        'body':None,'args':[plans[0]['globalRoomId'],0,100,None,'all'],'nativeSource':None}
                    # NATS flush establishes local SUB readiness, not remote
                    # route-interest propagation. This initial operation is a
                    # read-only history request; do not generalize to writes.
                    ready_id=str(uuid4())
                    for ready_attempt in range(20):
                        try:
                            reply=await caller.request('hollywood.agent.domain',domain,request_id=ready_id)
                            break
                        except BackplaneError as error:
                            if error.code!='AUTHORITY_UNAVAILABLE' or ready_attempt==19:raise
                            await asyncio.sleep(.05)
                    assert reply['status']==200 and len(reply['body']['events'])==1
                    revoked_revision={**domain,'homeRevision':domain['homeRevision']+1}
                    reply=await caller.request('hollywood.agent.domain',revoked_revision,request_id=str(uuid4()))
                    assert reply['status']==403
                    try:
                        await caller.request('hollywood.agent.authenticate',{'actorId':agent,
                            'credentialDigest':digest(token)},request_id=str(uuid4()))
                        raise AssertionError('Receiving node authenticated a reference as its own identity')
                    except BackplaneError as error:assert error.code=='AGENT_ACCESS_DENIED'
                    history=await caller.request('identity.read',payload,request_id=str(uuid4()))
                    assert len(history['events'])==1 and history['events'][0]['body']=='SYNTHETIC public room evidence'
                    # The exact same payload from another authenticated node is
                    # not the agent's home and cannot borrow its identity.
                    other=Client(a.connection,authority_id=authority,host_id=a.host_id,target_node_id=a.host_id)
                    try:
                        await other.request('identity.read',payload,request_id=str(uuid4()))
                        raise AssertionError('Non-home source impersonated the agent')
                    except BackplaneError as error:assert error.code=='AGENT_ACCESS_DENIED'
                    urls=[]
                    for idx,store in enumerate(stores):
                        transport=(a,b)[idx]
                        gateway=LocalAgentGateway(transport.connection,authority_id=authority,host_id=transport.host_id)
                        runner=web.AppRunner(application(store,federated_agent_gateway=gateway,
                            node_router=NodeRouter(store,gateway)));await runner.setup();runners.append(runner)
                        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
                        urls.append('http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]))
                    async with ClientSession() as session:
                        for idx,status in ((0,403),(1,200)):
                            async with session.get(urls[idx]+'/hollywood/v3/rooms',headers={'Authorization':'Bearer '+token}) as response:
                                assert response.status==status,await response.text()
                        headers={'Authorization':'Bearer '+token,'X-Hollywood-Authority':authority,
                            'X-Hollywood-Agent':agent}
                        async with session.get(urls[1]+'/hollywood/v3/rooms/'+plans[0]['globalRoomId'],headers=headers) as response:
                            assert response.status==200,await response.text()
                        async with session.get(urls[1]+'/hollywood/v3/rooms/'+plans[0]['globalRoomId']+'/history',headers=headers) as response:
                            assert response.status==200,await response.text()
                            remote_history=await response.json()
                            assert len(remote_history['events'])==1
                        async with session.get(urls[1]+'/hollywood/v3/rooms',headers=headers) as response:
                            assert response.status==200,await response.text()
                            catalog=await response.json()
                            (out/'catalog-readback.json').write_text(json.dumps(catalog,indent=2)+'\n')
                            assert {r['id'] for r in catalog['rooms']}=={p['globalRoomId'] for p in plans}
                            assert {r['ownerNodeId'] for r in catalog['rooms']}=={a.host_id,b.host_id}
                        # Real connector-prefixed reads must route by their
                        # captured delivery resource, not by the first URL word.
                        request_row=stores[0].post(owner_a,plans[0]['globalRoomId'],str(uuid4()),
                            {'kind':'request','audience':'room','recipientId':agent,
                             'body':'SYNTHETIC captured native preparation; no process or model'})
                        captured_delivery=request_row['deliveries'][0]['id']
                        with stores[0].pool.connection() as connection:
                            connection.execute("UPDATE hb_delivery SET state='claimed',generation=1,claim_id='SYNTHETIC-context-only',host=%s WHERE id=%s",
                                               (b.host_id,captured_delivery))
                        for suffix in ('context','briefing'):
                            async with session.get(urls[1]+'/hollywood/v3/connector/deliveries/'+captured_delivery+'/'+suffix,
                                                   headers=headers) as response:
                                assert response.status==200,await response.text()
                        with stores[0].pool.connection() as connection:
                            connection.execute("UPDATE hb_delivery SET state='cancelled',cancellation_requested=true WHERE id=%s",(captured_delivery,))
                        # Existing controller HTTP requires no SDK shim: the
                        # local node locates the exact source-host policy.
                        host_headers={'Authorization':'Bearer '+controller_token}
                        sample={'operationId':str(uuid4()),'machineIdentity':'a'*64,
                                'cpuSlots':8,'availableMemoryBytes':10737418240}
                        async with session.post(urls[1]+'/hollywood/v3/hosts/'+b.host_id+'/capacity',
                                                headers=host_headers,json=sample) as response:
                            assert response.status==200,await response.text()
                            host_receipt=await response.json()
                            assert host_receipt['accepted']
                        async with session.get(urls[1]+'/hollywood/v3/hosts/'+b.host_id+'/executors',
                                               headers=host_headers) as response:
                            assert response.status==200,await response.text()
                            assert (await response.json())['executors']==[]
                        async with session.post(urls[1]+'/hollywood/v3/rooms/'+plans[0]['globalRoomId']+'/messages',
                                                headers=host_headers,json={'body':'SYNTHETIC forbidden host-as-agent'}) as response:
                            assert response.status==403
                        async with session.get(urls[1]+'/hollywood/v3/hosts/'+a.host_id+'/executors',
                                               headers=host_headers) as response:
                            assert response.status==403
                        forged=Client(a.connection,authority_id=authority,host_id=a.host_id,target_node_id=a.host_id)
                        try:
                            await forged.request('hollywood.host.authenticate',
                                {'hostId':b.host_id,'credentialDigest':digest(controller_token)},request_id=str(uuid4()))
                            raise AssertionError('Other source host impersonated a controller')
                        except BackplaneError as error:assert error.code=='HOST_ACCESS_DENIED'
                        # Revoke after RPC authentication but before the Store
                        # transaction. The shared HTTP/domain fence must reject
                        # this write, not trust the earlier host lookup.
                        original_sample=stores[0].host_sample
                        def revoke_before_sample(*args):
                            with stores[0].pool.connection() as connection:
                                connection.execute('UPDATE hb_token SET revoked=true WHERE principal=%s',(controller,))
                            return original_sample(*args)
                        stores[0].host_sample=revoke_before_sample
                        rejected_operation=str(uuid4())
                        try:
                            async with session.post(urls[1]+'/hollywood/v3/hosts/'+b.host_id+'/capacity',
                                headers=host_headers,json={**sample,'operationId':rejected_operation}) as response:
                                assert response.status==403,await response.text()
                        finally:stores[0].host_sample=original_sample
                        with stores[0].pool.connection() as connection:
                            assert connection.execute('SELECT count(*) n FROM hb_admission_command WHERE principal=%s AND operation=%s',
                                (controller,rejected_operation)).fetchone()['n']==0
                    with stores[0].pool.connection() as c:
                        original=resolve_in_transaction(c,authority_id=authority,source_host=b.host_id,
                            principal_id=agent,home_node=b.host_id,credential_digest=digest(token))
                    with stores[0].pool.connection() as c:
                        c.execute('UPDATE hb_federated_agent_grant SET enabled=false WHERE principal=%s',(agent,))
                    with native_effects.transport(agent,digest(token),'GET','catalog','rooms',None,None,admission=original):
                        expect_denied(lambda:stores[0].catalog(agent))
                    # Existing placed worker changes login home without a new
                    # identity, profile, work attempt, or native process.
                    from hollywood_backbone import federation_home
                    moved=str(uuid4());moved_token=secrets.token_urlsafe(40)
                    stores[0].principal(owner_a,moved,'Moved QA worker','agent',moved_token)
                    with stores[0].pool.connection() as c:
                        c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(a.host_id,moved))
                        c.execute('INSERT INTO hb_placement(authority,agent,host) VALUES(%s,%s,%s)',(authority,moved,b.host_id))
                    transfer=federation_home.plan(stores[0],owner_a,moved,b.host_id)
                    try:
                        federation_home.accept(stores[1],owner_b,transfer,{},local_node=b.host_id)
                        raise AssertionError('Destination accepted without source fence')
                    except Denied:pass
                    with stores[0].pool.connection() as c:
                        c.execute('UPDATE hb_agent_role SET revision=revision+1 WHERE principal=%s',(moved,))
                    try:
                        federation_home.fence(stores[0],owner_a,transfer)
                        raise AssertionError('Stale home plan changed role')
                    except Conflict:pass
                    assert stores[0].authenticate(moved_token)['id']==moved
                    owned_room=str(uuid4())
                    stores[0].create_room(owner_a,owned_room,'Original worker room','SYNTHETIC retained room',moved)
                    with stores[0].pool.connection() as c:
                        c.execute('UPDATE hb_agent_role SET home_room=%s WHERE principal=%s',(owned_room,moved))
                    transfer=federation_home.plan(stores[0],owner_a,moved,b.host_id)
                    # A target reference holds foreign keys/routes, never a
                    # second room history or a bearer login.
                    install_reference(stores[1],owner_b,transfer['original'],enroll_execution_reference=True)
                    from hollywood_backbone.federation_identity import install_room_reference
                    source_room=stores[0].room_detail(owner_a,owned_room)['room']
                    install_room_reference(stores[1],owner_b,source_room,owner_node=a.host_id)
                    expect_denied(lambda:stores[1].room_detail(owner_b,owned_room))
                    fenced=federation_home.fence(stores[0],owner_a,transfer)
                    assert federation_home.fence(stores[0],owner_a,transfer)==fenced
                    expect_denied(lambda:stores[0].authenticate(moved_token))
                    expect_denied(lambda:stores[1].authenticate(moved_token))
                    received=federation_home.accept(stores[1],owner_b,transfer,fenced,local_node=b.host_id)
                    assert stores[1].authenticate(moved_token)['id']==moved
                    expect_denied(lambda:stores[0].authenticate(moved_token))
                    with stores[1].pool.connection() as c:
                        c.execute('UPDATE hb_token SET revoked=true WHERE principal=%s',(moved,))
                    assert federation_home.accept(stores[1],owner_b,transfer,fenced,local_node=b.host_id)==received
                    expect_denied(lambda:stores[1].authenticate(moved_token))
                    result={'passed':True,'independentStores':2,'organizationAuthorityShared':True,
                        'drainedHomeTransferKeepsIdentityAndCredential':True,
                        'noDualActiveLoginDuringHomeTransfer':True,
                        'existingControllerHttpOverHostLocalNats':True,
                        'connectorPrefixedContextAndBriefingReachOriginalOwner':True,
                        'hostControllerCannotBecomeAgentOrAnotherHost':True,
                        'hostCredentialRevokedBetweenRpcAndTransactionRejected':True,'staleRolePlanRejected':True,
                        'originalTransferRetryCannotUndoLaterRevocation':True,
                        'homeRoomRetainedAsNonCanonicalReference':True,
                        'homeOwnsCredentialOtherNodeOnlyReference':True,'bareHttpCredentialOnlyAcceptedAtHome':True,
                        'originalDisplayNameRetainedWithoutLocalOwnerCollision':True,'actualNatsHomeAttribution':True,
                        'otherNodeCannotImpersonateHome':True,'privateAudiencePreserved':True,
                        'grantRevocationRecheckedInDomainTransaction':True,'repeatEnrollmentSameRevision':True,
                        'actualHttpHomeLoginRemoteRoomAndCombinedCatalog':True,'nativeOrModelStarted':False,'nativeCrossNodeExecutionProof':False}
                    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');return result
                finally:
                    for runner in runners:await runner.cleanup()
                    for service in services:await service.stop()
            return check
        result=await qualify(binary,authority_id=authority,configure=configure)
        print(json.dumps(result,indent=2))
    finally:
        for store in stores:store.close()
        with psycopg.connect(**source,autocommit=True) as c:
            for schema in schemas:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

if __name__=='__main__':asyncio.run(main(sys.argv[1],sys.argv[2],Path(sys.argv[3])))
