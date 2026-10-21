"""Prepare same-identity adoption material only; no HTTP mutation/install/start."""
import hashlib,json,os,secrets,subprocess,time
from pathlib import Path
from uuid import uuid4
from hollywood_backbone.store import Store,digest
from lax_connector.host import machine_identity
from lax_connector.purpose_install import model_account
from lax_connector.tool_profile import read as read_tools

A='e488f19b-0f60-46ca-bd48-4fe544f06757';G='2d4a0daa-0514-475e-be1e-71decacbd5bc'
H='301c8b83-8cdb-5b9a-9d7f-3b2fa6f0b3f6';HOST='ec32d149-b563-589f-87c9-d5f468df1e79'
BASE=Path('/aosp/development/lax-three-context-20261018-r01');Q=BASE/'qualification'
ROOT=Path('/var/lib/lax-existing-global-adoption-20261021-r266')
RUNTIME=Path('/opt/harness/native-existing-realm-20261021-r265/venv')
PREVIEW=Path('/var/lib/lax-live-promotion-preview-20261021-r260')
def save(path,value):path.write_text(json.dumps(value,indent=2,default=str)+'\n');path.chmod(0o600)
def main():
 assert os.geteuid()==0
 assert not ROOT.exists(),'Inspect existing staged intent; do not regenerate identities or credentials'
 os.umask(0o077);ROOT.mkdir(mode=0o700)
 save(ROOT/'preparation-intent.json',{'at':time.time(),'authorityId':A,'agentId':G,'scope':__doc__,'liveMutationAuthorizedByThisScript':False})
 legacy_path=Path('/var/lib/harness')/A/'global/connector.json'
 legacy=json.loads(legacy_path.read_text())
 assert legacy['agentId']==G and legacy['hostId']==HOST and legacy['hollywoodUrl']=='http://127.0.0.1:18776'
 # No credential values leave subprocess stdout capture or administrator files.
 code="""import {loadHollywood} from '/home/aosp/.local/share/nextcomp-backend/releases/20261017-native-core-alpha110-r03/server/hollywood.mjs';
 const c=loadHollywood('/home/aosp/.local/share/nextcomp-backend/data');
 if(!c||c.invalid)throw Error('Invalid current human binding');
 const b=c.devices.get('owner')||c.defaultBinding||c.devices.values().next().value;
 process.stdout.write(JSON.stringify({authorityId:b.authorityId,principalId:b.principalId,defaultRoomId:b.defaultRoomId,url:b.url,token:b.token}));"""
 owner=json.loads(subprocess.check_output(['runuser','-u','aosp','--','node','--input-type=module','-e',code],text=True,stderr=subprocess.PIPE,timeout=20))
 assert owner['authorityId']==A and owner['principalId']==H and owner['url']=='http://127.0.0.1:18776'
 store=Store(json.loads(Path('/etc/harness/production/database.json').read_text()))
 try:
  with store.pool.connection() as c:
   c.execute('SET TRANSACTION READ ONLY')
   actor=c.execute('SELECT * FROM hb_principal WHERE id=%s',(G,)).fetchone()
   human=c.execute('SELECT * FROM hb_principal WHERE id=%s',(H,)).fetchone()
   assert actor['active'] and actor['administrator'] and actor['kind']=='agent' and str(actor['authority'])==A
   assert human['active'] and human['administrator'] and human['kind']=='human' and str(human['authority'])==A
   for principal,token in [(G,legacy['token']),(H,owner['token'])]:
    assert c.execute('SELECT 1 FROM hb_token WHERE principal=%s AND digest=%s AND NOT revoked',(principal,digest(token))).fetchone()
   role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s',(G,)).fetchone()
   place=c.execute('SELECT * FROM hb_placement WHERE agent=%s',(G,)).fetchone()
   context=c.execute('SELECT * FROM hb_agent_context WHERE principal=%s',(G,)).fetchone()
   assert str(place['host'])==HOST and place['enabled'] and str(role['home_room'])==owner['defaultRoomId']
   assert not c.execute("SELECT 1 FROM hb_delivery WHERE agent=%s AND state IN ('pending','claimed','running','cancel_requested','unknown')",(G,)).fetchone()
   assert not c.execute("SELECT 1 FROM hb_executor WHERE agent=%s AND state<>'stopped'",(G,)).fetchone()
   policy=c.execute('SELECT * FROM hb_host_admission WHERE host=%s',(HOST,)).fetchone();assert policy['machine_identity']==machine_identity()
 finally:store.close()
 current=Path('/etc/harness')/A/'environment.json';plan=json.loads((PREVIEW/'existing-realm-rollout-r265.json').read_text())
 assert hashlib.sha256(current.read_bytes()).hexdigest()==plan['environmentCandidate']['expectedLiveSha256']
 auth=Path(legacy['codexHome'])/'auth.json';account=model_account(auth)
 tools=PREVIEW/'native-tools.candidate-r265.json';read_tools(tools)
 identity={'authorityId':A,'agentId':G,'hostId':HOST,'workspaceId':str(uuid4()),'contextIds':{p:str(uuid4()) for p in ['reader','writer','responder']},'tokens':{p:secrets.token_urlsafe(40) for p in ['reader','writer','responder']}}
 save(ROOT/'identity-intent.json',identity)
 save(ROOT/'administrator.json',{'authorityId':A,'hostId':HOST,'hollywoodUrl':legacy['hollywoodUrl'],'token':owner['token']})
 save(ROOT/'legacy-context-and-role.json',{'context':context,'role':role,'placement':place,'legacyConnectorSha256':hashlib.sha256(legacy_path.read_bytes()).hexdigest()})
 spec={**identity,'credential':legacy['token'],'global':True,'hollywoodUrl':legacy['hollywoodUrl'],'machineIdentity':machine_identity(),'binary':'/opt/lax-native-pilot-20261020-r172/native/bin/codex-app-server','interpreter':str(RUNTIME/'bin/python'),'pythonRoot':str(RUNTIME/'lib/python3.12/site-packages'),'model':legacy['model'],'effort':legacy['effort'],'approvedModelAuthFile':str(auth),'nativeCollaborationChannel':str(Path('/etc/harness')/A/'collaboration'/HOST/'channel.json'),'toolProfileFile':str(tools),'serviceMemoryMaxBytes':8*1024**3}
 save(ROOT/'purpose-spec.json',spec)
 bindings=[{'purpose':p,'contextId':identity['contextIds'][p],'credentialDigest':digest(identity['tokens'][p])} for p in ['reader','writer','responder']]
 save(ROOT/'context-enrollment-request.json',{'operationId':'adopt-contexts:'+G+':r266','hostId':HOST,'workspaceId':identity['workspaceId'],'bindings':bindings})
 save(ROOT/'role-owner-request.json',{'operationId':'adopt-role-owner:'+G+':r266','revision':role['revision'],'primary':role['primary_role'],'purpose':role['purpose'],'homeRoomId':str(role['home_room']),'nativeGoalScope':role['native_goal_scope'],'decisionOwnerId':H})
 from hollywood_backbone.server import Role,ContextSystemConfiguration
 Role.model_validate(json.loads((ROOT/'role-owner-request.json').read_text()))
 ContextSystemConfiguration.model_validate(json.loads((ROOT/'context-enrollment-request.json').read_text()))
 receipt={'schema':'lax-existing-global-adoption-prepared/v1','at':time.time(),'authorityId':A,'agentId':G,'ownerHumanId':H,'globalRoomId':owner['defaultRoomId'],'sameHostId':HOST,'legacyRoleRevision':role['revision'],'legacyNativeThread':context['native_thread'],'modelAccountIdentityVerified':True,'modelAndEffortPreserved':[legacy['model'],legacy['effort']],'root':str(ROOT),'stagedFiles':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.iterdir() if p.is_file()},'futureNativeContextIds':identity['contextIds'],'modelsStarted':0,'httpMutations':0,'servicesInstalledOrStarted':0,'liveSourceRoutesChanged':False,'livePairingChanged':False,'phase':'prepared-private-intents; not canonical enrollment or sealed runtime','next':'Use exact staged identity/spec only after controlled API upgrade, original work/executor drain, role owner CAS, same-realm context enrollment, native collaboration installation and actual UID environment readiness. Never run against the production-ID copied DB.'}
 save(ROOT/'prepared.json',receipt);(Q/'actual-global-adoption-prepared-r266.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps({k:receipt[k] for k in ['root','authorityId','agentId','ownerHumanId','phase','modelsStarted','httpMutations','servicesInstalledOrStarted','livePairingChanged']}))
if __name__=='__main__':main()
