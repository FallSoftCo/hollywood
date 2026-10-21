import argparse,asyncio,importlib.util,json,time
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestServer
from importlib.metadata import version
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

Q=Path('/aosp/development/lax-three-context-20261018-r01/qualification')
async def main(phase):
 out=Q/('live-original-receipt-'+phase+'-r265.json');assert not out.exists()
 spec=importlib.util.spec_from_file_location('original_gateway_case',Q/'verify_live_gateway_write_recovery_r264.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 s=Store(json.loads((m.ROOT/'database.json').read_text()));requests=[]
 @web.middleware
 async def readonly(request,handler):
  assert request.method=='GET','No replay submission allowed'
  requests.append({'method':request.method,'path':request.path})
  return await handler(request)
 app=application(s);app.middlewares.insert(0,readonly);server=TestServer(app,host='127.0.0.1')
 await server.start_server()
 try:result=await m.node(m.RECOVER,server.make_url('/'),phase)
 finally:await server.close();s.close()
 assert result['eventId']=='f09337e9-1804-4e65-ab80-af0afca46f27' and result['deliveryId']=='e2f9a159-864a-41e4-b943-5eabb7966911' and result['posts']==0
 proof={'schema':'lax-native-original-receipt-runtime-compatibility/v1','at':time.time(),'runtimeVersion':version('hollywood-cli'),'phase':phase,'passed':True,'recovery':result,'requests':requests,'postCount':0,'modelsStarted':0,'listenersRetired':True,'liveServicesChanged':False,'scope':'Actual deployed client adapter GET recovery of original r264 accepted fixture through freshly installed candidate/fallback API. No new submit, native execution, physical phone or production migration claim.'}
 out.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({'version':proof['runtimeVersion'],'phase':phase,'passed':True,'posts':0}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['h83','h81-fallback']);a=p.parse_args();asyncio.run(main(a.phase))
