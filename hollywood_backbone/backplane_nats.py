"""NATS connection compatibility at Hollywood's transport boundary.

nats-py issue #1030: a cancelled/timed-out flush leaves its future in the
PONG FIFO. Resolving that completed future kills the receiver while the client
still reports connected. Consume its matching PONG without resolving it again.
Keep this local until the deployed upstream version includes the correction:
https://github.com/nats-io/nats.py/issues/1030
"""
from nats.aio.client import Client as NatsClient


class Client(NatsClient):
    """Preserve the NATS receiver after an abandoned flush acknowledgement."""

    async def _process_pong(self):
        if self._pongs:
            future = self._pongs.pop(0)
            if not future.done():
                future.set_result(True)
            self._pongs_received += 1
            self._pings_outstanding = 0
