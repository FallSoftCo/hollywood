"""Real PostgreSQL fences over recorded native admissions; rollback-only consent fixtures."""
import json,time,contextlib,copy
from pathlib import Path
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
def connect():
 return psycopg.connect(**json.loads(Path("/var/lib/lax-three-context-20261018-r01/database.json").read_text()),row_factory=dict_row)
from hollywood_backbone.store import Store,digest,Conflict,Denied
from hollywood_backbone.edit_plan_scope import participant_fences,EditError,consent_digest
from hollywood_backbone.physical_workspaces import grant
q=Path('/aosp/development/lax-three-context-20261018-r01/qualification')
out=q/'purpose-plan-fences-r40.json'
if out.exists():raise RuntimeError('Inspect existing qualification')
setup=json.loads(Path('/var/lib/lax-three-context-20261018-r01/setup-intent.json').read_text())
ids=['fb5efdc2-5639-501f-bba2-27abb9bdc6f2','512a8eb8-154b-51fc-8831-90927adb4da3']
root='6eda88b9-b8fe-5b6a-b1a9-8bda083071ad'
proof={'at':time.time(),'schema':'purpose-native-plan-fences/v1','mode':'Real QA PostgreSQL under actual app role; source scopes derived from recorded real native begin receipts and independently observed existing executors. Consent/physical-work-grant dictionaries are rollback-only fixtures, not claims of actual model consent, plan, filesystem mutation or acceptance. No executor/launch/native/process facts inserted.','modelsStarted':0}
with connect() as c:
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow()
 owner=store.actor(c,setup['credentials']['Owner']['principalId'])
 works=[c.execute('SELECT * FROM hb_work WHERE id=%s',(x,)).fetchone() for x in ids]
 physical=c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s',(root,)).fetchone()
 bridge=c.execute('SELECT * FROM hb_collaboration_bridge WHERE host=%s',(physical['host'],)).fetchone()
 votes=[]
 manifest={'participants':[]}
 for w in works:
  d=c.execute('SELECT * FROM hb_delivery WHERE id=%s',(w['delivery'],)).fetchone()
  begin=c.execute("SELECT result FROM hb_context_operation WHERE agent=%s AND purpose='writer' AND operation=%s",(w['owner'],'context-begin:'+d['native_turn'])).fetchone()
  scopes=[s for s in begin['result']['executionScopes'] if s['deliveryId']==str(d['id'])]
  assert len(scopes)==1
  scope=scopes[0]
  source={k:scope[k] for k in ('authorityId','agentId','hostId','deliveryId','claimId','generation','nativeThread','nativeTurn','roleRevision','contextId','executorId')}
  source.update(schema='hollywood-native-source/v3',executionScopeId=scope['id'],requestId=scope['sourceRequestId'],nativeGoalId=None,callId='rollback-consent-fixture',tool='collaborative_edit_plan',argumentsDigest='0'*64)
  old=c.execute('SELECT revision FROM hb_physical_grant WHERE workspace=%s AND work=%s',(root,w['id'])).fetchone()
  g=grant(c,owner,root,str(w['id']),{'revision':old['revision'] if old else 0,'workspaceRevision':physical['revision'],'workRevision':w['revision'],'state':'active'})['grant']
  target={'workspaceId':root,'workspaceRevision':physical['revision'],'grantRevision':g['revision'],'rootIdentitySha256':physical['root_identity_sha256'],'environmentSha256':physical['environment_sha256']}
  votes.append({'work':w['id'],'active':True,'source':source,'target':target,'bridge':bridge['controller'],'bridge_revision':bridge['revision'],'context_workspace':scope['workspaceId'],'peer_digest':None})
  manifest['participants'].append({'workId':str(w['id']),'workRevision':w['revision']})
 plan={'authority':owner['authority'],'goal':works[0]['goal'],'room':works[0]['room'],'integrator_work':works[0]['id']}
 votes[0]['peer_digest']=consent_digest([votes[1]])
 assert not c.execute('SELECT 1 FROM hb_agent_context WHERE principal=ANY(%s::uuid[])',([str(w['owner']) for w in works],)).fetchone(),'Fixture must exercise real purpose-only identities'
 participant_fences(store,c,plan,manifest,votes)
 proof['realPurposeOnlyAdmissionsAcceptedWithoutLegacyBinding']=True
 b=c.execute("SELECT active_turn FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(works[1]['owner'],)).fetchone()
 if not b['active_turn']:proof['actualIdlePeerConsentAdmissionAccepted']=True
 def deny(label,sql,args):
  try:
   with c.transaction():
    c.execute(sql,args)
    try:participant_fences(store,c,plan,manifest,votes)
    except (EditError,Denied,Conflict):raise RuntimeError('expected-denial')
    raise AssertionError(label+' incorrectly accepted')
  except RuntimeError as e:
   assert str(e)=='expected-denial'
   proof[label]=True
 deny('cancelledParticipantRejected','UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s',(works[1]['delivery'],))
 deny('unknownExecutorRejected',"UPDATE hb_executor SET state='unknown' WHERE id=%s",(votes[1]['source']['executorId'],))
 deny('changedNativeTurnRejected',"UPDATE hb_delivery SET native_turn='changed-rollback-only' WHERE id=%s",(works[1]['delivery'],))
 deny('revokedPhysicalGrantRejected',"UPDATE hb_physical_grant SET state='revoked' WHERE workspace=%s AND work=%s",(root,works[1]['id']))
 deny('disabledPurposeSystemRejected','UPDATE hb_context_system SET enabled=false WHERE agent=%s',(works[1]['owner'],))
 deny('changedContextRejected',"UPDATE hb_purpose_context SET context_id=%s WHERE agent=%s AND purpose='writer'",(UUID('00000000-0000-4000-8000-000000000040'),works[1]['owner']))
 participant_fences(store,c,plan,manifest,votes)
 c.rollback()
proof['allFixturesRolledBack']=True
out.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))

