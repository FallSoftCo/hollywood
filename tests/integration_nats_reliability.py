"""Real TCP broker: reproduce abandoned-PONG failure and qualify its correction.

The proxy holds precisely identified PONG frames, without slowing ordinary
requests or synthesizing broker replies. No production authority/model is used.
"""
import asyncio
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

from nats.aio.client import Client as OriginalClient
from nats.errors import FlushTimeoutError
from hollywood_backbone.backplane_nats import Client


class Proxy:
    def __init__(self, port):
        self.port = port
        self.held = asyncio.Queue()
        self.hold = False
        self.tasks = set()

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream_reader, upstream = await asyncio.open_connection('127.0.0.1', self.port)

        async def inbound():
            while data := await reader.read(65536):
                upstream.write(data)
                await upstream.drain()

        async def outbound():
            while data := await upstream_reader.readline():
                if data == b'PONG\r\n' and self.hold:
                    release = asyncio.Event()
                    await self.held.put(release)
                    await release.wait()
                writer.write(data)
                await writer.drain()

        pipes = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
        try:
            await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pipe in pipes:
                pipe.cancel()
            await asyncio.gather(*pipes, return_exceptions=True)
            upstream.close()
            writer.close()
            await asyncio.gather(upstream.wait_closed(), writer.wait_closed(), return_exceptions=True)
            self.tasks.discard(task)


async def scenario(cls, proxy, url, mode):
    errors = []

    async def error_cb(error):
        errors.append(type(error).__name__)

    nc = cls()
    await nc.connect(url, allow_reconnect=False, error_cb=error_cb)
    try:
        async def echo(message):
            await message.respond(message.data)

        await nc.subscribe('qa.echo', cb=echo)
        await nc.flush()
        proxy.hold = True
        pending = asyncio.create_task(nc.flush(timeout=.04 if mode == 'timeout' else 2))
        release = await asyncio.wait_for(proxy.held.get(), 2)
        if mode == 'cancel':
            pending.cancel()
        try:
            await pending
            raise AssertionError('Abandoned flush unexpectedly completed')
        except (asyncio.CancelledError, FlushTimeoutError):
            pass
        proxy.hold = False
        release.set()
        await asyncio.sleep(.03)
        alive = not nc._reading_task.done()
        if alive:
            answer = await nc.request('qa.echo', b'original-read-survived', timeout=1)
            assert answer.data == b'original-read-survived'
            await nc.flush()
        return {'mode': mode, 'receiverAlive': alive, 'errors': errors}
    finally:
        proxy.hold = False
        await nc.close()


async def main(binary):
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='nats-pong-') as temp:
        root = pathlib.Path(temp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with (root / 'broker.log').open('w+') as log:
            broker = subprocess.Popen([binary, '-a', '127.0.0.1', '-p', str(port)], stdout=log, stderr=log)
            proxy = Proxy(port)
            try:
                for _ in range(100):
                    try:
                        _, writer = await asyncio.open_connection('127.0.0.1', port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        assert broker.poll() is None, 'Private broker exited'
                        await asyncio.sleep(.02)
                server = await asyncio.start_server(proxy.accept, '127.0.0.1', 0)
                async with server:
                    url = 'nats://127.0.0.1:' + str(server.sockets[0].getsockname()[1])
                    baseline = await scenario(OriginalClient, proxy, url, 'cancel')
                    # Older releases log parser exceptions directly instead of
                    # delivering them to error_cb. The failed receiver is the
                    # observable defect; the retained broker-test log has its stack.
                    assert not baseline['receiverAlive'], baseline
                    fixed = [await scenario(Client, proxy, url, mode) for mode in ('cancel', 'timeout')]
                    assert all(x['receiverAlive'] and not x['errors'] for x in fixed), fixed
                if proxy.tasks:
                    await asyncio.wait_for(asyncio.gather(*proxy.tasks), 2)
                print(json.dumps({'passed': True, 'originalDependency': baseline, 'fixed': fixed,
                                 'seconds': time.monotonic() - start, 'models': 0,
                                 'productionCommands': 0, 'realTcpBroker': True}, indent=2))
            finally:
                broker.terminate()
                broker.wait(timeout=5)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
