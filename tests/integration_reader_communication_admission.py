"""Actual authenticated PG/HTTP Reader notice pipeline, rolled back.
Native turn/capacity metadata are explicit fixtures; no native process or model.
Original accepted QA request/result/artifacts remain unchanged after rollback.
"""
import argparse,asyncio,contextlib,json,secrets,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg import sql
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store,digest
from hollywood_backbone.server import application
def main(a):
 assert not a.output.exists()
 with psycopg.connect(**json.loads(a.database.read_text()),row_factory=dict_row) as c:
  class Borrow:
   @contextlib.contextmanager
   def connection(self):
    with c.transaction():yield c
  store=Store.__new__(Store);store.pool=Borrow()
  m=c.execute('SELECT * FROM hb_context_material WHERE id=%s',(a.input,)).fetchone();assert m['kind']=='input' and m['state']=='pending' and m['value'].get('peerCompletion')
  original_input=m;parentid=m['value']['parentWorkId'];original_work=c.execute('SELECT * FROM hb_context_material WHERE id=%s',(parentid,)).fetchone()
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(m['agent'],)).fetchone();assert not binding['active_turn']
  notice=c.execute('SELECT * FROM hb_event WHERE id=%s',(m['source'],)).fetchone();assert notice['kind']=='result' and notice['data']['type']=='peerCompletion'
  assert not c.execute('SELECT id FROM hb_delivery WHERE event=%s',(m['source'],)).fetchone()
  system=c.execute('SELECT * FROM hb_context_system WHERE agent=%s',(m['agent'],)).fetchone()
  if a.read_only_room:
   c.execute('UPDATE hb_principal SET administrator=false WHERE id=%s',(m['agent'],))
   parent_room=c.execute('SELECT room FROM hb_event WHERE id=%s',(original_work['source'],)).fetchone()['room']
   for room in set([notice['room'],parent_room]):
    c.execute('''INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request)
     VALUES(%s,%s,%s,true,false,false) ON CONFLICT(room,principal)
     DO UPDATE SET can_read=true,can_post=false,can_request=false''',(notice['authority'],room,m['agent']))
  token=secrets.token_urlsafe(40);td=digest(token);proto=c.execute('SELECT * FROM hb_token WHERE digest=%s',(binding['token_digest'],)).fetchone();names=list(proto);value={**proto,'digest':td,'revoked':False}
  c.execute(sql.SQL('INSERT INTO hb_token({}) VALUES({})').format(sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),[value[n] for n in names])
  c.execute("UPDATE hb_purpose_context SET token_digest=%s WHERE agent=%s AND purpose='reader'",(td,m['agent']))
  c.execute('UPDATE hb_placement SET enabled=true WHERE agent=%s',(m['agent'],))
  policy=c.execute('SELECT * FROM hb_host_admission WHERE host=%s',(system['host'],)).fetchone()
  assert policy['max_concurrent']>=1 and policy['reserved_interactive']==0
  c.execute('UPDATE hb_host_sample SET observed_at=clock_timestamp(),available_memory=%s,cpu_slots=1 WHERE host=%s',(policy['memory_reserve']+policy['memory_per_turn']*2,system['host']))
  async def run():
   proof={'at':time.time(),'mode':a.mode,'scope':'Real authenticated HTTP+PG. Same original pending peer signal/parent read as rollback fixture. Native turn and refreshed capacity facts are declared metadata fixtures;0native/model execution.','sourceInputId':a.input,'nonadminReadOnlyRoomFixture':a.read_only_room,'cases':[]}
   async with TestServer(application(store),host='127.0.0.1') as server:
    async with ClientSession() as client:
     async def call(action,value,turn='admission',purpose='reader',operation=None,auth=None,native_thread=None):
      payload={'operationId':operation or 'fixture-r24:'+str(uuid4()),'runtimeProtocol':'lax-purpose-runtime/v3','nativeThread':native_thread or binding['native_thread'],'nativeTurn':turn,'action':action,'value':value}
      async with client.post(server.make_url('/hollywood/v3/agents/'+str(m['agent'])+'/contexts/'+purpose),json=payload,headers={'Authorization':'Bearer '+(auth or token)}) as r:return r.status,await r.json(),payload['operationId']
     status,result,_=await call('reserve',{'materialIds':[a.input]})
     proof['readerReserve']={'status':status,'result':result}
     if a.mode=='baseline':
      assert status==(403 if a.read_only_room else 409);proof['status']='baseline_reproduced_missing_delivery_coupling';return proof
     assert status==200 and result['permit']['state']=='reserved' and result['admission']['reason']=='eligible'
     permit=result['permit']['id'];proof['cases'].append('canonical_reader_input_reserved_without_execution_delivery')
     denied,_,_=await call('reserve',{'materialIds':[a.input]},purpose='writer');assert denied==403;proof['cases'].append('reader_token_cannot_admit_writer')
     status,snapshot,op=await call('inbox',{'materialIds':[a.input]},turn='input-delivery');assert status==200 and snapshot['inputIds']==[a.input]
     turn='fixture-r24-native-metadata-'+str(uuid4())
     # Optional primaryInput packet discriminator omitted for the outer rollback
     # SQL fixture; actual native continuation qualifies current primaryInput=1.
     status,begun,_=await call('begin',{'materialIds':[a.input],'permitId':permit},turn=turn);assert status==200 and begun['state']=='running' and begun['executionScopes']==[]
     status,adopted,_=await call('inbox_adopt',{'snapshotOperationId':op},turn=turn);assert status==200 and a.input in adopted['materialIds'];proof['cases'].append('exact_canonical_input_snapshot_adopted_on_original_reader')
     status,applied,_=await call('peer_result',{'inputId':a.input,'body':'Rollback-only interpretation fixture; real peer artifact not edited.'},turn=turn);assert status==200 and applied['state']=='original_work_ready' and applied['responseRequired'] is False
     assert c.execute('SELECT state FROM hb_context_material WHERE id=%s',(parentid,)).fetchone()['state']=='pending'
     assert not c.execute('SELECT id FROM hb_delivery WHERE event=%s',(m['source'],)).fetchone();proof['cases'].append('same_original_parent_resumed_no_new_delivery_or_reply')
     status,ended,_=await call('end',{'status':'completed','permitId':permit},turn=turn);assert status==200 and ended['state']=='completed'
     permitrow=c.execute('SELECT state,terminal_status FROM hb_context_permit WHERE id=%s',(permit,)).fetchone();assert permitrow=={'state':'released','terminal_status':'completed'};proof['cases'].append('declared_native_metadata_permit_balanced')
     if a.read_only_room:
      wt=secrets.token_urlsafe(40);wd=digest(wt);wv={**proto,'digest':wd,'revoked':False}
      c.execute(sql.SQL('INSERT INTO hb_token({}) VALUES({})').format(sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),[wv[n] for n in names])
      wb=c.execute("SELECT native_thread FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(m['agent'],)).fetchone()
      c.execute("UPDATE hb_purpose_context SET token_digest=%s WHERE agent=%s AND purpose='writer'",(wd,m['agent']))
      denied,_,_=await call('reserve',{'materialIds':[parentid]},purpose='writer',auth=wt,native_thread=wb['native_thread'])
      assert denied==403
      proof['cases'].append('nonadmin_readonly_room_allows_reader_but_not_writer_execution')
     proof['status']='passed';return proof
  proof=asyncio.run(run());c.rollback()
  assert c.execute('SELECT * FROM hb_context_material WHERE id=%s',(a.input,)).fetchone()==original_input
  assert c.execute('SELECT * FROM hb_context_material WHERE id=%s',(parentid,)).fetchone()==original_work
  assert not c.execute('SELECT digest FROM hb_token WHERE digest=%s',(td,)).fetchone()
  proof.update(rollbackVerified=True,originalInputWorkUnchanged=True,newModels=0,productionChanged=False)
 a.output.write_text(json.dumps(proof,default=str,indent=2));print(json.dumps({k:v for k,v in proof.items() if k!='readerReserve'},default=str))
if __name__=='__main__':
 p=argparse.ArgumentParser()
 for n in ['database','output']:p.add_argument('--'+n,type=Path,required=True)
 p.add_argument('--read-only-room',action='store_true');p.add_argument('--input',required=True);p.add_argument('--mode',choices=['baseline','candidate'],required=True);main(p.parse_args())
