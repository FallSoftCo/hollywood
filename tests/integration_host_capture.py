"""Actual retained native executor and rollback-only HTTP authorization checks.
Pass the explicitly owned isolated single-context QA root; never production.
"""
import asyncio,contextlib,json,secrets,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store,digest
from hollywood_backbone.server import application
root=Path(sys.argv[1]);i=json.loads((root/'canary-intent.json').read_text());s=json.loads((root/'enterprise/installation-intent.json').read_text());enrollment=json.loads((root/'host-enrollment.json').read_text());controller=json.loads(Path(enrollment['configPath']).read_text());assert i['businessActions'] is False and s['name']=='Single-context native federation qualification'
owner=s['credentials']['Owner']['token'];agent=s['credentials']['Global']['token'];wrong_id=str(uuid4());wrong_token=secrets.token_urlsafe(40)
with psycopg.connect(**json.loads((root/'database.json').read_text()),row_factory=dict_row) as c:
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow()
 x=c.execute('SELECT * FROM hb_executor WHERE authority=%s AND agent=%s',(i['authorityId'],i['agentId'])).fetchone();assert x and x['state']=='stopped'
 c.execute("INSERT INTO hb_principal(id,authority,name,kind,administrator) VALUES(%s,%s,'Wrong host fixture','service',false)",(wrong_id,i['authorityId']));c.execute('INSERT INTO hb_token(digest,principal) VALUES(%s,%s)',(digest(wrong_token),wrong_id))
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    async def get(token,query='?view=capture',expected=200):
     async with client.get(server.make_url('/hollywood/v3/executors/'+str(x['id'])+query),headers={'Authorization':'Bearer '+token}) as response:
      v=await response.json();assert response.status==expected,(response.status,v);return v
    v=await get(controller['token']);assert v['capture']['snapshot']['delivery']['id']==str(x['delivery']);assert not v['capture']['admitsExecution'];assert v['capture']['role']['principal']==i['agentId'];encoded=json.dumps(v);assert not any(t in encoded for t in (owner,agent,controller['token'],wrong_token))
    assert 'capture' not in await get(controller['token'],'')
    await get(wrong_token,expected=403)
    c.execute('UPDATE hb_principal SET administrator=false WHERE id=%s',(i['agentId'],));await get(agent,expected=403);c.execute('UPDATE hb_principal SET administrator=true WHERE id=%s',(i['agentId'],))
    c.execute('UPDATE hb_delivery SET generation=generation+1 WHERE id=%s',(x['delivery'],));await get(controller['token'],expected=409);c.execute('UPDATE hb_delivery SET generation=generation-1 WHERE id=%s',(x['delivery'],))
    c.execute('UPDATE hb_token SET revoked=true WHERE digest=%s',(digest(controller['token']),));await get(controller['token'],expected=403);c.execute('UPDATE hb_token SET revoked=false WHERE digest=%s',(digest(controller['token']),))
    await get(controller['token'],'?view=invalid',400)
    return {'passed':True,'realPostgreSQLAndHTTP':True,'originalActualStoppedNativeExecutor':True,'ownControllerCaptureRead':True,'wrongControllerDenied':True,'nonAdministratorAgentDenied':True,'staleGenerationDenied':True,'revokedCredentialDenied':True,'defaultStatusUnchanged':True,'noCredentialsReturned':True,'readDoesNotAdmitExecution':True,'modelsStarted':0,'authorizationFixturesRolledBack':True}
 result=asyncio.run(run());c.rollback()
(root/'host-capture-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
