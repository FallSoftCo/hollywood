"""Actual PG/HTTP concurrent append; simulated native review is labelled below."""
import asyncio, json, os, secrets, time
from pathlib import Path
from uuid import uuid4
from aiohttp import web, ClientSession
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from lax_connector.conversation_io import ConversationIO
from lax_connector.room_tools import RoomTools
from lax_connector.room_reply_window import ReplyWindow, ReplyContextChanged

async def main():
    os.umask(0o077)
    root=Path(os.environ['REPLY_EVIDENCE']);root.mkdir(mode=0o700)
    uid=lambda:str(uuid4())
    store=Store(json.load(open(os.environ['REPLY_DATABASE'])));store.migrate()
    authority,room=uid(),uid()
    creds={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ('Owner','Global')}
    agents=[{'id':uid(),'token':secrets.token_urlsafe(40)} for _ in range(3)]
    store.bootstrap(authority,'Synthetic reply readiness QA',{'globalRoomId':uid(),'credentials':creds})
    owner=creds['Owner']['principalId']
    for i,a in enumerate(agents):
        store.principal(owner,a['id'],'QA peer '+str(i),'agent',a['token'])
    store.create_room(owner,room,'Synthetic reply comparison','No business work or workers',agents[0]['id'])
    for a in agents:
        store.membership(owner,room,a['id'],{'can_read':True,'can_post':True,'can_request':True},0)
    # Explicit non-provisioned legacy-room fixture. These logical test members
    # have no host enrollment/worker; do not qualify fleet initialization here.
    with store.pool.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM hb_room_agent_initialization WHERE room=%s',(room,)).fetchone()['n']==0
        c.execute('DELETE FROM hb_room_agent_generation WHERE room=%s',(room,))
    runner=web.AppRunner(application(store),access_log=None);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
    evidence={'authority':authority,'room':room,'label':'Real PG/HTTP; explicit legacy-room roster fixture; no semantic/model or fleet-initialization claim','trace':[]}
    ios=[]
    try:
        async with ClientSession() as session:
            async def request(a,method,path,payload=None):
                async with session.request(method,base+path,json=payload,headers={'Authorization':'Bearer '+a['token']}) as r:
                    v=await r.json();assert r.status==200,(r.status,v)
                    evidence['trace'].append({'agent':a['id'],'method':method,'path':path,'result':v});return v
            source=store.post(owner,room,'original-question',{'kind':'message','body':'Why did all agents answer?','audience':'room'})['event']
            tools=[]
            for i,a in enumerate(agents):
                io=ConversationIO(root/(str(i)+'.sqlite'),authority=authority,agent=a['id'],thread='qa-'+str(i));ios.append(io)
                io.stage(source);io.bind(source,{'turn':{'id':'turn-'+str(i)}})
                async def bound(method,path,payload=None,a=a):return await request(a,method,path,payload)
                t=RoomTools(authority_id=authority,agent_id=a['id'],thread_id='qa-'+str(i),request=bound,state=root/str(i),conversation_io=io)
                t.reply_window=ReplyWindow(io,None);io.reply_window=t.reply_window;tools.append(t)
            payload={'body':'All agent members received it, and each final was automatically published.',
                     'audience':'room','sourceEventId':source['id'],'responsePhase':'answer'}
            started=time.monotonic()
            results=await asyncio.gather(*(t.publish(room,payload,'first',turn='turn-'+str(i)) for i,t in enumerate(tools)),return_exceptions=True)
            elapsed=time.monotonic()-started
            winners=[i for i,v in enumerate(results) if isinstance(v,dict) and v.get('accepted')]
            losers=[i for i,v in enumerate(results) if isinstance(v,ReplyContextChanged)]
            assert len(winners)==1 and len(losers)==2,(winners,losers,[str(v) for v in results])
            assert 2<=elapsed<10,elapsed
            for i in losers:
                assert results[i].snapshot['peerReplies'][0]['author']==agents[winners[0]]['id']
            loser=losers[0];window=tools[loser].reply_window
            window.seen('turn-'+str(loser),results[loser].snapshot)
            addition=await tools[loser].publish(room,{**payload,'body':'Additional measured fact: the native Mac uses Astra at xhigh.'},'addition',turn='turn-'+str(loser))
            assert addition['accepted'] is True
            # Original accepted condition is reconciled BEFORE newer peer checks.
            original=await tools[winners[0]].publish(room,payload,'first',turn='turn-'+str(winners[0]))
            assert original==results[winners[0]]
            # A deliberate NEW model call is needed; reusing a rejected call
            # cannot silently publish it now that somebody has reconsidered.
            try:await tools[loser].publish(room,payload,'first',turn='turn-'+str(loser))
            except ReplyContextChanged:pass
            else:raise AssertionError('Rejected original call was admitted')
            other=losers[1];original_request=tools[other].request;lost_trace=[]
            async def lost_rejection(method,path,payload=None):
                value=await original_request(method,path,payload)
                lost_trace.append(method)
                if method=='POST':raise ConnectionError('Conditional result committed, HTTP response lost')
                return value
            tools[other].request=lost_rejection
            try:await tools[other].publish(room,payload,'lost-rejection',turn='turn-'+str(other))
            except ReplyContextChanged as error:
                assert error.snapshot['mayApply'] is False
            else:raise AssertionError('Lost conditional result was misclassified as accepted')
            assert lost_trace==['POST','GET'],lost_trace
            evidence['lostConditionalAckRecoveredWithoutResend']=lost_trace
            # Private peer replies and another human question cannot block this
            # public source or leak through its preview.
            fresh=store.post(owner,room,'second-question',{'kind':'message','body':'New source','audience':'room'})['event']
            private=store.post(agents[0]['id'],room,'private-answer',{'kind':'message','body':'PRIVATE MUST NOT LEAK','audience':'direct','recipientId':owner,'sourceEventId':fresh['id'],'responsePhase':'answer'})
            public=store.post(agents[1]['id'],room,'public-after-private',{'kind':'message','body':'Public answer','audience':'room','sourceEventId':fresh['id'],'responsePhase':'answer','replyCheck':{'observedThrough':fresh['seq']}})
            assert public['accepted']
            third=store.post(owner,room,'third-question',{'kind':'message','body':'Unphased replies count too','audience':'room'})['event']
            store.post(agents[0]['id'],room,'unphased-peer',{'kind':'message','body':'Useful unphased answer','audience':'room','sourceEventId':third['id']})
            checked=store.post(agents[1]['id'],room,'after-unphased-peer',{'kind':'message','body':'Candidate answer','audience':'room','sourceEventId':third['id'],'responsePhase':'answer','replyCheck':{'observedThrough':third['seq']}})
            assert checked['accepted'] is False and checked['code']=='REPLY_CONTEXT_CHANGED'
            assert checked['peerReplies'][0]['bodyPreview']=='Useful unphased answer'
            evidence['unphasedPeerRepliesIncluded']=True
            try:store.post(agents[0]['id'],room,'invalid-private-check',{'kind':'message','body':'Do not admit','audience':'direct','recipientId':owner,'sourceEventId':fresh['id'],'responsePhase':'answer','replyCheck':{'observedThrough':fresh['seq']}})
            except Exception as error:assert type(error).__name__=='Denied',error
            else:raise AssertionError('Private check must be rejected')
            evidence.update(elapsed=elapsed,winner=winners[0],losers=losers,addition=addition['event']['id'],passed=True)
    finally:
        for io in ios:io.close()
        await runner.cleanup();store.pool.close()
        (root/'result.private.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps({k:evidence[k] for k in ['passed','elapsed','winner','losers','addition']}))

asyncio.run(main())
