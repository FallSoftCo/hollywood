"""Real PG and authenticated HTTP protocol checks; all fixture writes roll back.
No model output, native execution or provider response is claimed by this test.
"""
import argparse,asyncio,contextlib,copy,json,secrets,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg import sql
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store,digest,Conflict
from hollywood_backbone.server import application
from hollywood_backbone import context_peers as peers
from hollywood_backbone.context_system import item
from hollywood_backbone.communication import completion
def main(a):
 assert not a.output.exists()
 with psycopg.connect(**json.loads(a.database.read_text()),row_factory=dict_row) as c:
  class Borrow:
   @contextlib.contextmanager
   def connection(self):
    with c.transaction():yield c
  store=Store.__new__(Store);store.pool=Borrow()
  parent=store.actor(c,a.parent);peer=store.actor(c,a.peer);source=store.readable_event(c,parent,a.source)
  initial={n:c.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(n))).fetchone()['n'] for n in ['hb_event','hb_delivery','hb_context_material','hb_principal','hb_token']}
  workid=str(uuid4())
  c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'running',%s)",(workid,parent['id'],source['id'],Jsonb({'objective':'Synthetic protocol fixture, no native/model run','acceptance':'Independent boundary checks','progress':[],'evidence':[]})))
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(parent['id'],)).fetchone()
  binding['reads']={workid:{'revision':1,'turn':'rollback-parent'}}
  queued=peers.queue(c,store,parent,binding,{'nativeTurn':'rollback-parent','operationId':'fixture:'+str(uuid4()),'value':{'workId':workid,'recipientId':str(peer['id']),'body':'Fixture protocol source only; no worker startup'}})
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(parent['id'],)).fetchone()
  assert peers.wait(c,store,parent,binding,{'nativeTurn':'rollback-parent','value':{'workId':workid}})['state']=='waiting'
  need=item(c,store,parent,queued['communicationId'],'need')
  accepted=peers.publish(c,store,parent,need,{'schema':'rollback-only-no-native-attestation'},None)
  request=store.readable_event(c,peer,accepted['event']['id'])
  result=store.event(c,peer,request['room'],'message','Synthetic canonical protocol fixture result, not model output.',audience=request['audience'],source=request['id'])
  delivery=c.execute("UPDATE hb_delivery SET state='completed',result_event=%s WHERE id=%s RETURNING *",(result,accepted['deliveries'][0]['id'])).fetchone()
  count_before=c.execute('SELECT count(*) AS n FROM hb_delivery').fetchone()['n']
  returned=completion(store,c,peer,request,delivery,result,'completed')
  notice=c.execute("SELECT * FROM hb_event WHERE source=%s AND data->>'type'='peerCompletion'",(result,)).fetchone()
  extra=c.execute('SELECT * FROM hb_delivery WHERE event=%s',(notice['id'],)).fetchall()
  incoming=item(c,store,parent,str(notice['id']),'input');assert incoming['value']['peerCompletion'] and not incoming['value']['responseRequired'] and incoming['value']['parentWorkId']==workid
  assert not c.execute("SELECT id FROM hb_context_material WHERE source=%s AND kind IN('need','work')",(notice['id'],)).fetchall()
  desired=notice['kind']=='result' and not extra and str(returned)==str(notice['id']) and c.execute('SELECT count(*) AS n FROM hb_delivery').fetchone()['n']==count_before
  native_before={'kind':notice['kind'],'extraExecutionDeliveries':len(extra),'nativeInputMappedToOriginalWork':True,'noReplyOrNewWork':True,'desiredContract':desired}
  applied=peers.apply(c,store,parent,{}, {'nativeTurn':'rollback-reader','value':{'inputId':str(incoming['id']),'body':'Fixture interpretation, no semantic model claim'}})
  assert applied['state']=='original_work_ready' and item(c,store,parent,workid,'work')['value']['resumeRevision']==1
  try:peers.apply(c,store,parent,{}, {'nativeTurn':'rollback-reader','value':{'inputId':str(incoming['id']),'body':'duplicate'}})
  except Conflict:pass
  else:raise AssertionError('Duplicate result application resumed parent again')
  def external(label):
   pid=str(uuid4());tok=secrets.token_urlsafe(40)
   c.execute("INSERT INTO hb_principal(id,authority,name,kind,administrator) VALUES(%s,%s,%s,'agent',false)",(pid,parent['authority'],label))
   c.execute("INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true)",(parent['authority'],source['room'],pid))
   proto=c.execute('SELECT * FROM hb_token WHERE principal=%s LIMIT 1',(parent['id'],)).fetchone()
   names=list(proto);values={**proto,'digest':digest(tok),'principal':pid,'revoked':False}
   c.execute(sql.SQL('INSERT INTO hb_token({}) VALUES({})').format(sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),[values[n] for n in names])
   assert str(store.authenticate(tok)['id'])==pid
   return store.actor(c,pid),tok
  sender,token=external('rollback-external-recipient');other,othertoken=external('rollback-other-agent')
  ex=store.post_in_transaction(c,sender,str(source['room']),'fixture:'+str(uuid4()),{'kind':'request','body':'Fixture external request','audience':'direct','recipientId':str(peer['id']),'expectsReply':True})
  req=store.readable_event(c,peer,ex['event']['id']);answer=store.event(c,peer,req['room'],'message','Fixture external answer',recipient=sender['id'],audience='direct',source=req['id'])
  d=c.execute("UPDATE hb_delivery SET state='completed',result_event=%s WHERE id=%s RETURNING *",(answer,ex['deliveries'][0]['id'])).fetchone()
  completion(store,c,peer,req,d,answer,'completed')
  exnotice=c.execute("SELECT * FROM hb_event WHERE source=%s AND data->>'type'='peerCompletion'",(answer,)).fetchone()
  async def http():
   async with TestServer(application(store),host='127.0.0.1') as server:
    async with ClientSession() as client:
     async def call(path,tok):
      async with client.get(server.make_url('/hollywood/v3/'+path),headers={'Authorization':'Bearer '+tok}) as r:return r.status,await r.json()
     status,value=await call('inbox/events/'+str(exnotice['id']),token)
     otherstatus,othervalue=await call('inbox/events/'+str(exnotice['id']),othertoken)
     fstatus,feed=await call('inbox?after='+str(exnotice['seq']-1),token);assert fstatus==200
     return {'recipientReadStatus':status,'otherAgentReadStatus':otherstatus,'recipientInboxHasNotice':any(e['eventId']==str(exnotice['id']) for e in feed['events'])}
  external_http=asyncio.run(http())
  external_extra=c.execute('SELECT id FROM hb_delivery WHERE event=%s',(exnotice['id'],)).fetchall()
  assert not c.execute('SELECT id FROM hb_context_material WHERE agent=%s',(sender['id'],)).fetchall()
  assert external_http['otherAgentReadStatus']==403
  eventcount=c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
  assert completion(store,c,peer,req,{**d,'cancellation_requested':True},answer,'completed') is None
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==eventcount
  proof={'at':time.time(),'mode':a.mode,'scope':'Real PG+authenticated HTTP, rollback-only synthetic canonical fixture.0model/native runs; no semantic evidence claimed.','native':native_before,'external':{**external_http,'extraExecutionDeliveries':len(external_extra)},'privacyBoundaryHeld':True,'sameParentResumedOnce':True,'cancelledSourceCreatesNoNotice':True,'newModels':0,'productionChanged':False}
  a.output.write_text(json.dumps({**proof,'status':'pre_assert_evidence'},indent=2))
  if a.mode=='candidate':
   assert desired and not external_extra and external_http['recipientReadStatus']==200 and external_http['recipientInboxHasNotice'];proof['status']='passed'
  else:
   assert not desired and len(extra)==1 and len(external_extra)==1 and external_http['recipientReadStatus']==403 and not external_http['recipientInboxHasNotice'];proof['status']='baseline_reproduced_frozen_contract_failure'
  c.rollback()
  assert {n:c.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(n))).fetchone()['n'] for n in initial}==initial
  proof['rollbackVerified']=True
 a.output.write_text(json.dumps(proof,indent=2));print(json.dumps(proof))
if __name__=='__main__':
 p=argparse.ArgumentParser()
 for n in ['database','parent','peer','source','output']:p.add_argument('--'+n,required=True,type=Path if n in ['database','output'] else str)
 p.add_argument('--mode',choices=['baseline','candidate'],required=True);main(p.parse_args())
