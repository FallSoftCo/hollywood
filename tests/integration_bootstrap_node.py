"""Actual PostgreSQL bootstrap, repetition and ownership-conflict rollback; no models."""
import json,secrets,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from hollywood_backbone.store import Store,Conflict

def main(file):
 source=json.loads(Path(file).read_text());schema='qa_bootstrap_node_'+uuid4().hex
 with psycopg.connect(**source,autocommit=True) as c:c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
 store=Store({**source,'options':source.get('options','')+' -c search_path='+schema})
 try:
  store.migrate();authority=str(uuid4());node=str(uuid4())
  plan={'nodeId':node,'globalRoomId':str(uuid4()),'credentials':{k:{'principalId':str(uuid4()),'token':secrets.token_urlsafe(40)} for k in ['Owner','Global']}}
  assert store.bootstrap(authority,'Synthetic node bootstrap',plan)['created']
  assert not store.bootstrap(authority,'Synthetic node bootstrap',plan)['created']
  with store.pool.connection() as c:
   assert {str(x['home_node']) for x in c.execute('SELECT home_node FROM hb_principal WHERE authority=%s',(authority,)).fetchall()}=={node}
   assert str(c.execute('SELECT home_node FROM hb_room WHERE id=%s',(plan['globalRoomId'],)).fetchone()['home_node'])==node
  try:store.bootstrap(authority,'Synthetic node bootstrap',{**plan,'nodeId':str(uuid4())})
  except Conflict:pass
  else:raise AssertionError('Existing home moved')
  try:store.placement(plan['credentials']['Owner']['principalId'],node,'Synthetic host','synthetic-fingerprint',[],plan['credentials']['Global']['principalId'])
  except ValueError:pass
  else:raise AssertionError('Invalid capability shape accepted')
  with store.pool.connection() as c:
   assert c.execute('SELECT 1 FROM hb_host WHERE id=%s',(node,)).fetchone() is None
  legacy=str(uuid4());old={'globalRoomId':str(uuid4()),'credentials':{k:{'principalId':str(uuid4()),'token':secrets.token_urlsafe(40)} for k in ['Owner','Global']}}
  store.bootstrap(legacy,'Synthetic legacy',old)
  store.post(old['credentials']['Owner']['principalId'],old['globalRoomId'],'bootstrap-node-legacy-event',{'kind':'message','body':'Synthetic retained history','audience':'room'})
  try:store.bootstrap(legacy,'Synthetic legacy',{**old,'nodeId':str(uuid4())})
  except Conflict:pass
  else:raise AssertionError('Active legacy implicitly moved')
  with store.pool.connection() as c:
   assert all(x['home_node'] is None for x in c.execute('SELECT home_node FROM hb_principal WHERE authority=%s',(legacy,)).fetchall())
  print(json.dumps({'passed':True,'realPostgres':True,'newBinding':True,'repeat':True,'changedHomeRejected':True,'legacyWithHistoryRejected':True,'modelCalls':0}))
 finally:
  store.close()
  with psycopg.connect(**source,autocommit=True) as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
if __name__=='__main__':main(sys.argv[1])
