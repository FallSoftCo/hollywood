"""Finite real-host probe; only reads the host's existing local health endpoint."""
import asyncio
import json
from pathlib import Path
import socket
import ssl
import sys
import time
import urllib.request
from uuid import uuid4

import nats
from hollywood_backbone.backplane import HostBackplane, connect_host
from hollywood_backbone.host_backend import NextCompReadAdapter


async def main(config_path, mode):
    path = Path(config_path)
    config = json.loads(path.read_text())
    nc = await connect_host(server=config['url'], host_id=config['host'],
                            password=config['password'],
                            ca_file=str(path.parent / config['ca']))
    stop = asyncio.Event()
    def health():
        with urllib.request.urlopen(config['backend'], timeout=3) as response:
            return response.status
    async def probe(request):
        return {'hostName': socket.gethostname(), 'hostId': config['host'],
                'sourceHostId': request.source_host_id,
                'backendHealthHttp': await asyncio.to_thread(health),
                'modelsStarted': False}
    async def finish(request):
        asyncio.get_running_loop().call_later(.2, stop.set)
        return {'stoppingOwnedQaWorker': True}
    adapter = NextCompReadAdapter(config['localBackend'], config['localCredentialFile']) if config.get('localBackend') else None
    handlers = {'backend.health': probe, 'qa.finish': finish}
    if adapter:
        handlers.update(adapter.handlers())
    client = HostBackplane(nc, authority_id=config['authority'], host_id=config['host'],
                           authorized_sources={config['peer']},
                           handlers=handlers,
                           control_operations=frozenset({'qa.finish'}))
    try:
        if mode == 'serve':
            await client.start()
            (path.parent / f"{config['role']}-ready.json").write_text(json.dumps({
                'role': config['role'], 'hostname': socket.gethostname(),
                'hostId': config['host'], 'tlsVerifiedConnection': True,
                'at': time.time()}))
            if config['role'] == 'gpu':
                started = time.monotonic()
                result = await client.request(config['peer'], 'backend.health', {},
                                               request_id=str(uuid4()), timeout=5)
                (path.parent / 'gpu-to-cpu.json').write_text(json.dumps({
                    'result': result, 'roundtripMs': (time.monotonic()-started)*1000,
                    'tlsVerifiedConnection': True}, indent=2))
                if adapter:
                    status = await client.request(config['peer'], 'runtime.status', {}, request_id=str(uuid4()), timeout=10)
                    catalog = await client.request(config['peer'], 'sessions.list', {'limit': 5}, request_id=str(uuid4()), timeout=10)
                    (path.parent / 'gpu-to-cpu-catalog.json').write_text(json.dumps({'runtime': status, 'sessionCount': len(catalog['threads']), 'refreshPending': catalog.get('refreshPending'), 'readOnly': True}, indent=2))
            try:
                await asyncio.wait_for(stop.wait(), 180)
            except asyncio.TimeoutError:
                pass
        elif mode == 'probe':
            started = time.monotonic()
            result = await client.request(config['peer'], 'backend.health', {},
                                           request_id=str(uuid4()), timeout=5)
            output = {'result': result, 'roundtripMs': (time.monotonic()-started)*1000,
                      'tlsVerifiedConnection': True, 'physicalHostProbe': True}
            print(json.dumps(output))
            (path.parent / f"{config['role']}-to-peer.json").write_text(json.dumps(output, indent=2))
        elif mode == 'catalog':
            started = time.monotonic()
            status = await client.request(config['peer'], 'runtime.status', {}, request_id=str(uuid4()), timeout=10)
            catalog = await client.request(config['peer'], 'sessions.list', {'limit': 5}, request_id=str(uuid4()), timeout=10)
            result = {'runtime': status, 'sessionCount': len(catalog['threads']), 'refreshPending': catalog.get('refreshPending'), 'roundtripMs': (time.monotonic()-started)*1000, 'readOnly': True}
            (path.parent / (config['role']+'-to-peer-catalog.json')).write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        elif mode == 'finish':
            print(json.dumps(await client.request(config['peer'], 'qa.finish', {},
                                                   request_id=str(uuid4()), lane='control', timeout=5)))
        else:
            raise ValueError('Unknown mode')
    finally:
        await client.close()
        await nc.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1], sys.argv[2]))
