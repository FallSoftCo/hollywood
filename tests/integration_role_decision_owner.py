"""Real PG routing invariants with explicit frozen role-work facts; full rollback."""
import argparse,json,time,hashlib
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,digest,json_value,Conflict,Denied
from hollywood_backbone.attention import human_owner
parser=argparse.ArgumentParser()
parser.add_argument('--database-config',type=Path,required=True)
parser.add_argument('--cohort-directory',type=Path,required=True,help='Explicit disposable enrolled two-human fixture, all mutations roll back')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();base=args.cohort_directory
a=json.loads((base/'public.json').read_text());plan=json.loads((base/'installation-intent.json').read_text());b=json.loads((base/'source-human.json').read_text())
s=Store(json.loads((args.database_config).read_text()));checks={}
def mark(name,value):assert value,name;checks[name]=bool(value)
try:
 with s.pool.connection() as c:
  before=c.execute('SELECT count(*) AS n FROM hb_event WHERE authority=%s',(a['authority'],)).fetchone()['n']
  with c.transaction(force_rollback=True):
   actor=s.actor(c,a['agent']);person_b=s.actor(c,b['principalId']);owner=plan['credentials']['Owner']['principalId']
   role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s',(actor['id'],)).fetchone()
   snapshot=json_value(role)
   source=s.event(c,person_b,a['room'],'message','Ordinary engineering context from B, not decision authority.')
   event=s.event(c,actor,a['room'],'control','Frozen role work fixture',recipient=actor['id'],source=source,
      data={'type':'roleWork','schema':'lax-role-work/v1','roleRevision':role['revision'],'roleSha256':digest(snapshot)})
   wid=str(uuid4());value={'roleInitiative':{'roleSnapshot':snapshot}}
   c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",(wid,actor['id'],event,Jsonb(value)))
   e=s.readable_event(c,actor,event)
   mark('captured-role-owner-A-not-source-human-B',str(human_owner(c,e))==owner and owner!=b['principalId'])
   ordinary=s.event(c,person_b,a['room'],'request','Ordinary directly addressed request',recipient=actor['id'])
   mark('ordinary-human-B-request-still-targets-B',str(human_owner(c,s.readable_event(c,actor,ordinary)))==b['principalId'])
   with c.transaction(force_rollback=True):
    c.execute('UPDATE hb_agent_role SET decision_owner=%s,revision=revision+1 WHERE principal=%s',(b['principalId'],actor['id']))
    mark('captured-original-owner-does-not-follow-current-reassignment',str(human_owner(c,e))==owner)
   with c.transaction(force_rollback=True):
    legacy={k:v for k,v in snapshot.items() if k!='decision_owner'}
    c.execute("UPDATE hb_context_material SET value=%s WHERE id=%s",(Jsonb({'roleInitiative':{'roleSnapshot':legacy}}),wid))
    c.execute("UPDATE hb_event SET data=jsonb_set(data,'{roleSha256}',%s) WHERE id=%s",(Jsonb(digest(legacy)),event))
    old=s.readable_event(c,actor,event)
    try:human_owner(c,old)
    except Conflict:mark('old-ownerless-snapshot-does-not-infer-fallback',True)
    else:raise AssertionError('Legacy snapshot inferred new owner')
   with c.transaction(force_rollback=True):
    c.execute('UPDATE hb_principal SET active=false WHERE id=%s',(owner,))
    try:human_owner(c,e)
    except Denied:mark('inactive-owner-denied-without-fallback-B',True)
    else:raise AssertionError('Inactive decision owner accepted')
   with c.transaction(force_rollback=True):
    c.execute('UPDATE hb_principal SET administrator=false WHERE id=%s',(owner,))
    try:human_owner(c,e)
    except Denied:mark('owner-missing-room-rights-denied',True)
    else:raise AssertionError('Decision owner inferred room access')
   with c.transaction(force_rollback=True):
    human=s.actor(c,owner)
    try:s._admin_role(c,human,a['agent'],role['primary_role'],role['purpose'],str(role['home_room']),role['revision'],role['native_goal_scope'],a['agent'])
    except Denied:mark('agent-cannot-be-enrolled-as-human-owner',True)
    else:raise AssertionError('Agent role owner accepted')
  after=c.execute('SELECT count(*) AS n FROM hb_event WHERE authority=%s',(a['authority'],)).fetchone()['n']
  mark('all-frozen-routing-facts-rolled-back',before==after)
 proof={'at':time.time(),'checks':checks,'scope':'Real PostgreSQL, explicit frozen RoleWork facts, not actual model behavior or new Attention creation','modelsStarted':0}
 args.output.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
finally:s.close()
