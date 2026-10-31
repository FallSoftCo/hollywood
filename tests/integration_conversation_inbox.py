"""Real PG + public HTTP fan-out; no placement, executor, model or task claim."""
import asyncio,json,os,secrets
from pathlib import Path
from uuid import uuid4
from aiohttp import web,ClientSession
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

def uid():return str(uuid4())
async def main():
    os.umask(0o077)
    root=Path(os.environ['CONVERSATION_EVIDENCE']);root.mkdir(mode=0o700)
    store=Store(json.loads(Path(os.environ['CONVERSATION_DATABASE']).read_text()))
    authority,room=uid(),uid();credentials={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ('Owner','Global')}
    actors={k:{'id':uid(),'token':secrets.token_urlsafe(40)} for k in ('A','B','Outsider')}
    (root/'intent.private.json').write_text(json.dumps({'authority':authority,'room':room,'credentials':credentials,'actors':actors}))
    owner=credentials['Owner']['principalId'];global_agent=credentials['Global']['principalId']
    store.bootstrap(authority,'SYNTHETIC conversational fanout',{'globalRoomId':room,'credentials':credentials})
    for name,a in actors.items():store.principal(owner,a['id'],name,'agent',a['token'])
    for name in ('A','B'):store.membership(owner,room,actors[name]['id'],{'can_read':True,'can_post':True,'can_request':True},0)
    # New installations and human-created rooms are actual conversations,
    # not merely administrator-readable rooms with no listening participants.
    with store.pool.connection() as c:
        members=c.execute('SELECT principal FROM hb_membership WHERE authority=%s AND room=%s AND can_read',(authority,room)).fetchall()
        assert {owner,global_agent}.issubset({str(m['principal']) for m in members})
    child=uid();store.create_room(owner,child,'Creator membership QA','Synthetic membership',actors['A']['id'])
    with store.pool.connection() as c:
        members=c.execute('SELECT principal FROM hb_membership WHERE authority=%s AND room=%s AND can_read',(authority,child)).fetchall()
        assert {owner,actors['A']['id']}.issubset({str(m['principal']) for m in members})
    runner=web.AppRunner(application(store));await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
    trace=[]
    try:
        async with ClientSession() as session:
            async def get(name,after=None,limit=100):
                path='inbox?view=conversation&limit='+str(limit)+(('&after='+str(after)) if after is not None else '')
                async with session.get(base+path,headers={'Authorization':'Bearer '+actors[name]['token']}) as response:
                    data=await response.json();trace.append({'actor':name,'path':path,'status':response.status,'result':data});assert response.status==200,data;return data
            start=await get('A');assert start['events']==[]
            public=store.post(owner,room,uid(),{'kind':'message','audience':'room','body':'Human room message for all members'})
            private=store.post(owner,room,uid(),{'kind':'message','audience':'direct','recipientId':actors['A']['id'],'body':'Private human input'})
            assert [e['id'] for e in store.inbox(global_agent,0,100,False,'conversation')['events']]==[public['event']['id']]
            # Runtime absent at send time still gets its outstanding input on first read.
            assert [e['id'] for e in (await get('A'))['events']]==[public['event']['id'],private['event']['id']]
            # Joining after publication is context/history, not fresh instruction replay.
            store.membership(owner,room,actors['Outsider']['id'],{'can_read':True,'can_post':True,'can_request':True},0)
            one,two,out=await asyncio.gather(get('A',start['nextCursor']),get('B',start['nextCursor']),get('Outsider',start['nextCursor']))
            pub=public['event']['id'];priv=private['event']['id']
            assert [e['id'] for e in one['events']]==[pub,priv]
            assert [e['id'] for e in two['events']]==[pub]
            assert out['events']==[]
            assert all(e['data']['authorKind']=='human' for e in one['events'])
            page=await get('A',start['nextCursor'],1);tail=await get('A',page['nextCursor'],1)
            assert [page['events'][0]['id'],tail['events'][0]['id']]==[pub,priv]
            reconnect=await get('A',start['nextCursor']);assert reconnect['events']==one['events']
            # Revocation applies even to previously unconsumed room traffic.
            store.membership(owner,room,actors['B']['id'],{'can_read':False,'can_post':False,'can_request':False},1)
            assert (await get('B',start['nextCursor']))['events']==[]
            # A valid member can speak without an execution claim, but ordinary
            # requests are communication too, without creating executor claims.
            async def agent_post(kind,expected):
                payload={'operationId':'publication-'+kind,'kind':kind,'audience':'room','body':'Synthetic agent communication','recipientId':global_agent if kind=='request' else None}
                async with session.post(base+'rooms/'+room+'/messages',json=payload,headers={'Authorization':'Bearer '+actors['A']['token']}) as response:
                    value=await response.json();assert response.status==expected,(response.status,value)
                    return value
            spoken=await agent_post('message',200)
            assert spoken['event']['author']==actors['A']['id'] and spoken['event']['data']['authorKind']=='agent'
            requested=await agent_post('request',200)
            assert requested['deliveries']==[]
            assert requested['event']['data']['authorKind']=='agent'
            human_request=store.post(owner,room,uid(),{'kind':'request','audience':'room','recipientId':actors['A']['id'],'body':'Human communication request'})
            assert human_request['deliveries']==[]
            assert human_request['event']['data']['humanInputEventId']==human_request['event']['id']
            # Source correlation survives a peer hop and does not replace the
            # original accepted human receipt with execution state.
            progress=store.post(actors['A']['id'],room,uid(),{'kind':'message','audience':'room','recipientId':global_agent,'sourceEventId':human_request['event']['id'],'responsePhase':'update','body':'Peer progress'})
            answer=store.post(global_agent,room,uid(),{'kind':'message','audience':'room','sourceEventId':progress['event']['id'],'responsePhase':'answer','body':'Deliberate answer'})
            assert answer['event']['data']['humanInputEventId']==human_request['event']['id']
            assert answer['event']['data']['responsePhase']=='answer'
            original=store.post(owner,room,uid(),{'kind':'request','audience':'room','recipientId':actors['A']['id'],'body':'Another bounded input'})
            assert original['deliveries']==[]
            # An existing delegated administrator may manage communication
            # identities/rooms without manufacturing an executor reservation.
            child_agent,admin_room=uid(),uid()
            async def admin_call(token,method,path,payload,expected=200):
                async with session.request(method,base+path,json=payload,headers={'Authorization':'Bearer '+token}) as response:
                    value=await response.json();assert response.status==expected,(response.status,value);return value
            principal_body={'operationId':'native-admin-principal','id':child_agent,'name':'Scoped synthetic peer','kind':'agent','enrollmentCredential':secrets.token_urlsafe(40)}
            enrolled=await admin_call(credentials['Global']['token'],'POST','principals',principal_body)
            assert await admin_call(credentials['Global']['token'],'POST','principals',principal_body)==enrolled
            await admin_call(actors['A']['token'],'POST','principals',{**principal_body,'operationId':'denied-non-admin','id':uid()},403)
            await admin_call(credentials['Global']['token'],'POST','principals',{**principal_body,'operationId':'denied-authority-escalation','id':uid(),'administrator':True},403)
            await admin_call(credentials['Global']['token'],'POST','rooms',{'operationId':'native-admin-room','id':admin_room,'name':'Synthetic native administrator room','purpose':'Communication only','ownerId':child_agent})
            await admin_call(credentials['Global']['token'],'PUT','rooms/'+admin_room+'/membership',{'operationId':'native-admin-membership','principalId':actors['A']['id'],'can_read':True,'can_post':True,'can_request':True,'revision':0})
            role=store.role_read(global_agent,child_agent)
            await admin_call(credentials['Global']['token'],'PUT','agents/'+child_agent+'/role',{'operationId':'native-admin-role','primary':'developer','purpose':'Synthetic local implementation','homeRoomId':admin_room,'revision':role['revision']})
            async with session.get(base+'administration/commands/native-admin-principal',headers={'Authorization':'Bearer '+credentials['Global']['token']}) as response:
                original_receipt=await response.json();assert response.status==200
                assert original_receipt['actorId']==global_agent and original_receipt['acceptedResult']==enrolled
            with store.pool.connection() as c:
                counts={table:c.execute('SELECT count(*) AS n FROM '+table+' WHERE authority=%s',(authority,)).fetchone()['n'] for table in ('hb_delivery','hb_executor')}
            assert counts=={'hb_delivery':0,'hb_executor':0},counts
            (root/'result.json').write_text(json.dumps({'passed':True,'bootstrapAndCreatorMembership':True,'sameCanonicalPublicMessageBothAgents':True,'privateIsolation':True,'membershipRevocation':True,'orderedPagingReconnect':True,'counts':counts,'modelCalls':0,'deployed':False},indent=2))
            print('PASS real PG/HTTP: same human event to two agents; private isolation; paging/reconnect; revocation; zero deliveries/executors/models')
    finally:
        (root/'trace.private.json').write_text(json.dumps(trace,indent=2));await runner.cleanup();store.close()
asyncio.run(main())
