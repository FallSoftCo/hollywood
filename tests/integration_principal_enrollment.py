"""Real PostgreSQL/HTTP enrollment checks; all rows rolled back, no workers."""
import asyncio, contextlib, json, secrets, sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store, digest
from hollywood_backbone.server import application

root=Path(sys.argv[1])
intent=json.loads((root/'enterprise/installation-intent.json').read_text())
assert intent['name']=='Single-context native federation qualification'
owner=intent['credentials']['Owner']['token']
agent=intent['credentials']['Global']['token']
with psycopg.connect(**json.loads((root/'database.json').read_text()),row_factory=dict_row) as c:
 c.execute('BEGIN')
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction(): yield c
 store=Store.__new__(Store); store.pool=Borrow()
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    async def post(p, token=owner, status=200):
     async with client.post(server.make_url('/hollywood/v3/principals'),json=p,headers={'Authorization':'Bearer '+token}) as r:
      body=await r.json(); assert r.status==status,(r.status,body)
      return body
    key=secrets.token_urlsafe(40); identity=str(uuid4())
    p={'id':identity,'name':'Rollback digest '+str(uuid4()),'kind':'agent','enrollmentCredentialDigest':digest(key)}
    first=await post(p); assert first['created']
    assert str(store.authenticate(key)['id'])==identity
    try: store.authenticate(digest(key))
    except Exception: pass
    else: raise AssertionError('Digest accepted as bearer credential')
    assert not (await post(p))['created']
    await post({**p,'enrollmentCredentialDigest':digest(secrets.token_urlsafe(40))},status=409)
    await post({**p,'administrator':True},status=409)
    await post({**p,'enrollmentCredential':key},status=400)
    await post({k:v for k,v in p.items() if k!='enrollmentCredentialDigest'},status=400)
    c.execute('UPDATE hb_token SET revoked=true WHERE principal=%s',(identity,))
    await post(p,status=409)
    raw={'id':str(uuid4()),'name':'Rollback legacy '+str(uuid4()),'kind':'agent','enrollmentCredential':secrets.token_urlsafe(40)}
    assert (await post(raw))['created']
    admin={**raw,'id':str(uuid4()),'name':'Rollback admin '+str(uuid4()),'administrator':True,'enrollmentCredential':secrets.token_urlsafe(40)}
    assert (await post(admin))['created']
    assert c.execute('SELECT administrator FROM hb_principal WHERE id=%s',(admin['id'],)).fetchone()['administrator']
    assert not (await post({k:v for k,v in admin.items() if k!='administrator'}))['created']
    await post({**admin,'administrator':False},status=409)
    await post({**admin,'id':str(uuid4())},token=agent,status=403)
    await post({**admin,'id':str(uuid4()),'kind':'human'},status=400)
    return {'passed':True,'realPostgreSQLAndHTTP':True,'digestEnrollmentAuthenticatesOriginalSecretOnly':True,'legacyEnrollmentPreserved':True,'revocationNotRestored':True,'privilegeChangesRequireExplicitReconciliation':True,'onlyHumanAdministratorMayCreateDelegatedAdministrator':True,'fixturesRolledBack':True,'workersOrModelsStarted':0}
 result=asyncio.run(run());c.rollback()
(root/'principal-enrollment-qualification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
