"""Actual TLS broker loss/reconnect; original operation is not replayed."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from nats.errors import Error as NATSError

from hollywood_backbone.backplane import HostBackplane, connect_host
from hollywood_backbone.host_service import HostService


async def state(service, expected, timeout=20):
    async def wait():
        while True:
            service.changed.clear()
            if service.state['state'] in ({expected} if isinstance(expected,str) else expected):
                return
            await service.changed.wait()
    await asyncio.wait_for(wait(), timeout)


async def qualify(binary, fixture_path):
    fixture = Path(fixture_path)
    cpu = json.loads((fixture/'cpu.json').read_text())
    gpu = json.loads((fixture/'gpu.json').read_text())
    config = json.loads((fixture/'server.json').read_text())
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        probe.bind((config['host'], config['port']))
    with tempfile.TemporaryDirectory(prefix='backplane-service-') as temporary:
        path = Path(temporary)
        service = HostService({'version':1,'authorityId':cpu['authority'],'hostId':cpu['host'],
            'sourceAdmission':'broker-enrollment','server':cpu['url'],
            'password':cpu['password'],'caFile':str(fixture/'ca.pem'),
            'localBackend':cpu['localBackend'],'localCredentialFile':cpu['localCredentialFile'],
            'stateFile':str(path/'state.json')})
        invoked = 0
        held, release = asyncio.Event(), asyncio.Event()
        async def work(request):
            nonlocal invoked
            invoked += 1
            held.set()
            await release.wait()
            return {'finished':True}
        async def count(request):
            return {'count':invoked}
        service.handlers.update({'fixture.work':work,'fixture.count':count})
        transitions=[]
        original_state=service.publish_state
        def record_state(value,error=None):
            transitions.append({'state':value,'error':error})
            original_state(value,error)
        service.publish_state=record_state
        log=(path/'broker.log').open('w')
        brokers=[]
        def launch():
            child=subprocess.Popen(['/usr/bin/timeout','90',binary,'-c',str(fixture/'server.json')],stdout=log,stderr=log,start_new_session=True)
            brokers.append(child)
            return child
        broker=launch()
        task=asyncio.create_task(service.run())
        connection=None
        request=None
        try:
            await state(service,'ready')
            assert broker.poll() is None
            connection=await connect_host(server=gpu['url'],host_id=gpu['host'],password=gpu['password'],ca_file=str(fixture/'ca.pem'))
            client=HostBackplane(connection,authority_id=cpu['authority'],host_id=gpu['host'],handlers={},authorized_sources={cpu['host']})
            result=await client.request(cpu['host'],'host.describe',{},request_id=str(uuid4()),lane='control')
            assert result['state']=='ready'
            status=await client.request(cpu['host'],'runtime.status',{},request_id=str(uuid4()))
            request=asyncio.create_task(client.request(cpu['host'],'fixture.work',{},request_id=str(uuid4()),timeout=.5))
            await asyncio.wait_for(held.wait(),3)
            os.killpg(broker.pid,signal.SIGTERM)
            await asyncio.to_thread(broker.wait,5)
            await state(service,{'unavailable','waiting_for_connection'},5)
            assert invoked==1
            # The old handler is still owned while its response transport is gone.
            release.set()
            try:
                await request
            except (NATSError, asyncio.TimeoutError):
                pass
            else:
                raise AssertionError('Lost transport unexpectedly returned success')
            request=None
            await state(service,'waiting_for_connection')
            if connection:
                await connection.close()
                connection=None
            broker=launch()
            await state(service,'ready',25)
            assert broker.poll() is None and invoked==1
            connection=await connect_host(server=gpu['url'],host_id=gpu['host'],password=gpu['password'],ca_file=str(fixture/'ca.pem'))
            client=HostBackplane(connection,authority_id=cpu['authority'],host_id=gpu['host'],handlers={},authorized_sources={cpu['host']})
            value=await client.request(cpu['host'],'fixture.count',{},request_id=str(uuid4()))
            assert value['count']==1
            value=await client.request(cpu['host'],'host.describe',{},request_id=str(uuid4()),lane='control')
            assert value['state']=='ready' and value['error'] is None
            return {'passed':True,'realTlsBroker':True,'actualLocalBackendConnected':status.get('connected'),
                'disconnectImmediatelyUnavailable':True,'reconnected':True,'oldOperationInvocations':invoked,
                'noAutomaticOperationReplay':True,'healthyStateClearsOldError':True,
                'modelOrNativeWorkersStarted':False,'transitions':transitions.copy()}
        except BaseException:
            print(json.dumps({'failureState':service.state,'transitions':transitions,'taskDone':task.done(),'connectionClosed':service.connection.is_closed if service.connection else None,'brokerExit':broker.poll(),'brokerLog':(path/'broker.log').read_text()[-4000:]}),flush=True)
            raise
        finally:
            release.set()
            failures=[]
            if request:
                request.cancel()
                await asyncio.gather(request,return_exceptions=True)
            service.stop()
            try:
                await asyncio.wait_for(task,15)
            except BaseException as error:
                failures.append(error)
            if connection:
                try:
                    await connection.close()
                except BaseException as error:
                    failures.append(error)
            for child in brokers:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid,signal.SIGTERM)
                        await asyncio.to_thread(child.wait,5)
                    except BaseException as error:
                        try:
                            os.killpg(child.pid,signal.SIGKILL)
                            await asyncio.to_thread(child.wait,5)
                        except BaseException as kill_error:
                            failures.append(kill_error)
                        failures.append(error)
            log.close()
            if failures:
                raise BaseExceptionGroup('Owned fixture cleanup failures',failures)



if __name__=='__main__':
    print(json.dumps(asyncio.run(qualify(sys.argv[1],Path(sys.argv[2]))),indent=2))
