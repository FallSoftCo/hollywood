"""Real PG/HTTP conversational question and approval without delivery claims."""
import asyncio,json,os,secrets
from pathlib import Path
from uuid import uuid4
from aiohttp import web,ClientSession
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

async def main():
 os.umask(0o077);root=Path(os.environ['ROOM_ATTENTION_EVIDENCE']);root.mkdir(mode=0o700)
 store=Store(json.load(open(os.environ['ROOM_NATIVE_DATABASE'])));store.migrate();uid=lambda:str(uuid4())
 authority,room,agent=uid(),uid(),uid();credentials={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ('Owner','Global')};token=secrets.token_urlsafe(40);owner=credentials['Owner']['principalId']
 (root/'fixture.intent.private.json').write_text(json.dumps({'authority':authority,'room':room,'agent':agent,'credentials':credentials,'token':token}))
 store.bootstrap(authority,'SYNTHETIC conversational attention',{'globalRoomId':uid(),'credentials':credentials});store.principal(owner,agent,'Callback agent','agent',token);store.create_room(owner,room,'Attention QA','Native callback communication',agent)
 source=store.post(owner,room,'original-human-input',{'kind':'message','audience':'room','body':'Complete the local work; ask if necessary.'})['event']
 runner=web.AppRunner(application(store),access_log=None);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
 trace=[]
 try:
  async with ClientSession() as session:
   async def req(who,method,path,payload=None,expected=200):
    async with session.request(method,base+path,json=payload,headers={'Authorization':'Bearer '+(token if who=='agent' else credentials['Owner']['token'])}) as response:
     v=await response.json();trace.append({'who':who,'method':method,'path':path,'status':response.status,'value':v});assert response.status==expected,(response.status,v);return v
   for kind in ('question','approval'):
    identity=uid();path='attention/'+identity
    payload={'id':identity,'conversationVersion':1,'sourceEventId':source['id'],'nativeThread':'original-native-thread','nativeTurn':'original-native-turn','nativeSlot':kind,'method':'item/tool/requestUserInput' if kind=='question' else 'item/commandExecution/requestApproval','summary':'Choose the requested action','request':{'questions':[{'id':'choice','header':'Choice','question':'Which option?'}],'isBlocking':True} if kind=='question' else {'command':'printf qualification','cwd':'/private-qa','reason':'Synthetic native callback'}}
    created=await req('agent','POST','attention',payload);assert created['attention']['delivery'] is None
    assert (await req('agent','POST','attention',payload))['scope']==created['scope']
    scope=created['scope'];response={'answers':{'choice':{'answers':['Original answer']}}} if kind=='question' else {'decision':'decline'}
    answer={'operationId':'one-original-response','scope':scope,'response':response}
    await req('agent','POST',path+'/response',answer,403)
    answered=await req('human','POST',path+'/response',answer);assert answered['attention']['state']=='response_submitted'
    assert await req('human','POST',path+'/response',answer)==answered
    changed={**answer,'response':{'answers':{'choice':{'answers':['Changed']}}} if kind=='question' else {'decision':'accept'}}
    await req('human','POST',path+'/response',changed,409)
    for state in ('native_submitted','closed'):
     closed=await req('agent','POST',path+'/acknowledge',{'operationId':'original-'+state,'scope':scope,'state':state});assert closed['attention']['state']==state
    await req('human','POST',path+'/response',{**answer,'operationId':'late-answer'},409)
   inbox=await req('human','GET','rooms/'+room+'/attention?after=0&limit=100');assert len(inbox['attention'])==2
   with store.pool.connection() as c:claims=c.execute('SELECT count(*) AS n FROM hb_delivery WHERE authority=%s',(authority,)).fetchone()['n']
   assert claims==0
   result={'passed':True,'questionAndApproval':True,'onlyOriginalHumanCanAnswer':True,'sameOperationResponseDeduplicated':True,'changedResponseRejected':True,'closedCallbackRejectsLateResponse':True,'existingAttentionInbox':True,'executionDeliveries':claims,'modelCalls':0,'productionChanged':False};(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
 finally:
  (root/'http.private.json').write_text(json.dumps(trace,indent=2));await runner.cleanup();store.close()
asyncio.run(main())
