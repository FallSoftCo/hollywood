"""Actual controller HTTP registry and administrator grant, rollback-only."""
import asyncio,contextlib,json,time
from pathlib import Path
from uuid import uuid4
from psycopg import Rollback
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
import psycopg
from psycopg.rows import dict_row
def connect():
 return psycopg.connect(**json.loads(Path("/var/lib/lax-three-context-20261018-r01/database.json").read_text()),row_factory=dict_row)
from hollywood_backbone.store import Store,Conflict,Denied
from hollywood_backbone.server import application
from hollywood_backbone.physical_workspaces import registered
q=Path('/aosp/development/lax-three-context-20261018-r01/qualification');out=q/'purpose-target-registry-http-r46.json'
if out.exists():raise RuntimeError('Inspect original qualification')
base=Path('/var/lib/lax-three-context-20261018-r01');setup=json.loads((base/'setup-intent.json').read_text())
authority=setup['authorityId'];agent=setup['credentials']['Global']['principalId'];work='fb5efdc2-5639-501f-bba2-27abb9bdc6f2';root='6eda88b9-b8fe-5b6a-b1a9-8bda083071ad'
controller=json.loads((Path('/var/lib/harness')/authority/'hosts'/setup['hostId']/'controller.json').read_text())
proof={'schema':'purpose-target-registry-http/v1','at':time.time(),'mode':'Real accepted purpose identity and original Work; authenticated controller read/admin grant HTTP and PostgreSQL. Temporary per-Work grant rolls back. No model, native process, executor, claim or consent facts inserted. Existing automatic scoped grant protocol retained.','modelsStarted':0}
with connect() as c:
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow()
 actor=store.actor(c,setup['credentials']['Owner']['principalId'])
 assert not c.execute('SELECT 1 FROM hb_agent_context WHERE principal=%s',(agent,)).fetchone()
 w=c.execute('SELECT * FROM hb_work WHERE id=%s',(work,)).fetchone()
 ctx=c.execute("SELECT context_id,native_thread FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
 r=c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s',(root,)).fetchone()
 old=c.execute('SELECT * FROM hb_physical_grant WHERE workspace=%s AND work=%s',(root,work)).fetchone();assert not old,'Current test must reproduce missing derived grant'
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    async def http(method,path,value=None,token=None):
     async with client.request(method,server.make_url('/hollywood/v3/'+path),json=value,headers={'Authorization':'Bearer '+(token or controller['token'])}) as response:return response.status,await response.json()
    status,x=await http('GET','collaboration/physical-targets/'+work)
    assert status==200 and x['targets']==[],(status,x)
    proof['purposeOnlyOwnerRegistryAvailableBeforeAutomaticWorkGrant']=True
    payload={'operationId':'qa-target-grant:'+str(uuid4()),'revision':0,'workspaceRevision':r['revision'],'workRevision':w['revision'],'state':'active'}
    status,g=await http('PUT','physical-workspaces/'+root+'/grants/'+work,payload,setup['credentials']['Owner']['token'])
    assert status==200 and g['grant']['context_id']==str(ctx['context_id']),(status,g)
    status,x=await http('GET','collaboration/physical-targets/'+work)
    assert status==200 and len(x['targets'])==1 and x['targets'][0]['grant_revision']==g['grant']['revision'],(status,x)
    proof['sameExistingScopedGrantAppearsWithExactPurposeIdentityAndRevision']=True
    target={'workspaceId':root,'workspaceRevision':r['revision'],'grantRevision':g['grant']['revision'],'environmentSha256':r['environment_sha256'],'rootIdentitySha256':r['root_identity_sha256']}
    source={'schema':'hollywood-native-source/v3','agentId':agent,'hostId':setup['hostId'],'contextId':str(ctx['context_id'])}
    registered(c,actor['authority'],source,work,target)
    with c.transaction() as tx:
     c.execute("UPDATE hb_physical_grant SET state='revoked' WHERE workspace=%s AND work=%s",(root,work))
     try:registered(c,actor['authority'],source,work,target)
     except (Conflict,Denied):proof['explicitWorkGrantRevocationStillFencesMutation']=True
     else:raise AssertionError('Revoked Work grant accepted')
     raise Rollback()
    with c.transaction() as tx:
     c.execute('UPDATE hb_context_system SET enabled=false WHERE agent=%s',(agent,))
     status,x=await http('GET','collaboration/physical-targets/'+work);assert status==403,(status,x)
     proof['disabledPurposeEnrollmentCannotFallBackToLegacy']=True;raise Rollback()
    fresh=json.loads((base/'fresh-global-r44/installation-intent.json').read_text())
    # A valid administrator of a different actual authority cannot inspect this Work.
    status,x=await http('GET','collaboration/physical-targets/'+work,token=fresh['credentials']['Owner']['token']);assert status==403,(status,x)
    proof['otherAuthorityAdministratorCannotReadOriginalWorkTarget']=True
 asyncio.run(run());c.rollback()
 assert not c.execute('SELECT 1 FROM hb_physical_grant WHERE workspace=%s AND work=%s',(root,work)).fetchone()
proof['allFixturesRolledBack']=True
out.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))

