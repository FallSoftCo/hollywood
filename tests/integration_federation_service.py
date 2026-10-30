"""Actual authenticated NATS Services API; no custom directory or model calls."""
import asyncio
import json
import sys
from uuid import uuid4
from hollywood_backbone.backplane import BackplaneError
from hollywood_backbone.federation_service import Service, Client, discover
from integration_backplane import qualify


async def main(binary):
    def configure(a,b):
        async def check():
            seen=[]
            async def read(request):
                seen.append(request)
                return {'sourceHostId': request.source_host_id,'servedByHostId': request.target_host_id}
            async def uncertain(request):
                seen.append(request)
                await asyncio.sleep(.2)
                return {'originalOperationId':request.payload['operationId']}
            services=[Service(t.connection,authority_id=t.authority_id,host_id=t.host_id,
                handlers={'read':read,'write':uncertain}) for t in (a,b)]
            try:
                for s in services:await s.start()
                async def broken_announcement(message):
                    await message.respond(b'not valid JSON')
                malformed=await a.connection.subscribe('$SRV.INFO.hollywood',cb=broken_announcement)
                try:
                    found=await discover(a.connection,timeout=.3)
                finally:
                    await malformed.unsubscribe()
                assert {n['hostId'] for n in found}=={a.host_id,b.host_id}
                client=Client(a.connection,authority_id=a.authority_id,host_id=a.host_id)
                result=await client.request('read',{},request_id=str(uuid4()))
                assert result['sourceHostId']==a.host_id and result['servedByHostId'] in {a.host_id,b.host_id}
                # One organization may span independent stores. A node-owned
                # resource must never load-balance into another node's store.
                node_a=Client(a.connection,authority_id=a.authority_id,host_id=a.host_id,target_node_id=a.host_id)
                node_b=Client(a.connection,authority_id=a.authority_id,host_id=a.host_id,target_node_id=b.host_id)
                assert (await node_a.request('read',{},request_id=str(uuid4())))['servedByHostId']==a.host_id
                assert (await node_b.request('read',{},request_id=str(uuid4())))['servedByHostId']==b.host_id
                await services[0].stop()
                result=await client.request('read',{},request_id=str(uuid4()))
                assert result['servedByHostId']==b.host_id
                try:
                    await node_a.request('read',{},request_id=str(uuid4()))
                    raise AssertionError('Unavailable resource owner silently replaced by another store')
                except BackplaneError as error:assert error.code=='AUTHORITY_UNAVAILABLE'
                assert (await node_b.request('read',{},request_id=str(uuid4())))['servedByHostId']==b.host_id
                original=str(uuid4());before=len(seen)
                try:
                    await client.request('write',{'operationId':original},request_id=original,timeout=.05)
                    raise AssertionError('Expected uncertain reply')
                except BackplaneError as e:assert e.code=='REPLY_UNKNOWN'
                await asyncio.sleep(.25)
                assert len(seen)==before+1 and seen[-1].request_id==original
                await services[1].stop()
                try:
                    await client.request('read',{},request_id=str(uuid4()))
                    raise AssertionError('Expected no responders')
                except BackplaneError as e:assert e.code=='AUTHORITY_UNAVAILABLE'
                return {'passed':True,'standardNatsServicesDiscovery':True,'realAuthenticatedBroker':True,
                    'serviceInstances':2,'malformedAnnouncementDoesNotHideHealthyNodes':True,'survivingInstanceServesAfterOtherStops':True,
                    'originalSourceHostPreserved':True,'uncertainMutationAttempts':1,
                    'modelsStarted':0,'databaseReplicationRequired':False,
                    'explicitNodeDestinationPreservedWithinOneOrganization':True,
                    'offlineOwnerNeverFallsThroughToAnotherStore':True,
                    'adapterQualificationOnlyNotPhysicalHostOrNativeExecutionProof':True}
            finally:
                for s in services:await s.stop()
        return check
    result=await qualify(binary,configure=configure,response_max=4)
    print(json.dumps(result,indent=2))


if __name__=='__main__':asyncio.run(main(sys.argv[1]))
