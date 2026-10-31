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
    await req('agent','POST','attention',{**payload,'conversationVersion':True},400)
    created=await req('agent','POST','attention',payload);assert created['attention']['delivery'] is None
    assert (await req('agent','POST','attention',payload))['scope']==created['scope']
    notifications=await req('human','GET','inbox?after=0&limit=100')
    hint=next(x for x in notifications['events'] if x.get('attentionId')==identity)
    assert hint['notify'] is True and hint['reason']==kind
    pending_notifications=await req('human','GET','inbox/pending?after=0&limit=100')
    assert any(x.get('attentionId')==identity for x in pending_notifications['events'])

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
    resolved=await req('human','GET','inbox/events/'+hint['eventId'])
    assert resolved['hint']['notify'] is False and resolved['hint']['reason']=='attention_resolved'
    assert resolved['hint']['revision']>hint['revision']

   meaningful=store.post(agent,room,'result-before-runtime-state',{'kind':'message','audience':'room','body':'Meaningful result before lifecycle state','sourceEventId':source['id'],'responsePhase':'answer'})['event']
   answer_hint=await req('human','GET','inbox/events/'+meaningful['id'])
   assert answer_hint['hint']['notify'] is True and answer_hint['hint']['reason']=='request_result'
   update=store.post(agent,room,'quiet-intermediate-update',{'kind':'message','audience':'room','body':'Routine intermediate update','sourceEventId':source['id'],'responsePhase':'update'})['event']
   update_hint=await req('human','GET','inbox/events/'+update['id'])
   assert update_hint['hint']['notify'] is False
   service=uid();store.principal(owner,service,'Synthetic runtime observer','service',secrets.token_urlsafe(40))
   with store.pool.connection() as c:
    observer=store.actor(c,service)
    lifecycle=store.event(c,observer,room,'progress','Original executor stopped; original evidence retained',data={'type':'executorState','deliveryState':'unknown','resourceState':'stopped','deliveryId':uid(),'executorId':uid(),'generation':1})
   history=await req('human','GET','rooms/'+room+'/history?view=conversation&before=9007199254740991&limit=1')
   actions=await req('human','GET','rooms/'+room+'/history?view=activity&before=9007199254740991&limit=1')
   assert history['events'][0]['id']==update['id']
   assert actions['events'][0]['id']==str(lifecycle) and actions['events'][0]['data']['deliveryState']=='unknown'
   inbox=await req('human','GET','rooms/'+room+'/attention?after=0&limit=100');assert len(inbox['attention'])==2
   with store.pool.connection() as c:claims=c.execute('SELECT count(*) AS n FROM hb_delivery WHERE authority=%s',(authority,)).fetchone()['n']
   assert claims==0
   result={'passed':True,'questionAndApproval':True,'onlyOriginalHumanCanAnswer':True,'sameOperationResponseDeduplicated':True,'changedResponseRejected':True,'closedCallbackRejectsLateResponse':True,'existingAttentionInbox':True,'actionableNativeQuestionAndApprovalHints':True,'resolvedQuestionsDoNotRenotify':True,'deliberateAnswerNotifiesWithoutClaims':True,'routineUpdatesStayQuiet':True,'lifecycleSeparateBeforeLimit':True,'strictVersion':True,'executionDeliveries':claims,'modelCalls':0,'productionChanged':False};(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
 finally:
  (root/'http.private.json').write_text(json.dumps(trace,indent=2));await runner.cleanup();store.close()
asyncio.run(main())
