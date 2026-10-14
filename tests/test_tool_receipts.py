from unittest.mock import AsyncMock

import pytest

from hollywood_room.runtime import Runtime
from hollywood_room.store import Store


async def test_tool_receipts_reconnect_restart_unknown_and_conflict(tmp_path):
    path = tmp_path / 'room.db'
    store = Store(path)
    try:
        store.ensure_binding('lobby', 'maya')
        with store.db:
            store.db.execute("UPDATE binding SET thread='native-thread'")
        def runtime():
            return Runtime(store, binary='unused', home=tmp_path, state=tmp_path, workspace=tmp_path)
        rt = runtime()
        rt.active[('lobby', 'maya')] = {'visibility': 'room', 'delivery': {'depth': 0}}
        params = {'threadId': 'native-thread', 'turnId': 'native-turn', 'callId': 'call',
                  'tool': 'hollywood_ask', 'arguments': {'question': 'Which target?'}}
        first = await rt.tool('item/tool/call', params)
        assert first['success']
        rt.active.clear()
        assert await rt.tool('item/tool/call', params) == first
        assert not (await rt.tool('item/tool/call', {**params, 'arguments': {'question': 'Changed'}}))['success']
        store.close()
        store = Store(path)
        rt = runtime()
        assert await rt.tool('item/tool/call', params) == first
        assert len(store.rows('SELECT * FROM attention')) == 1
        rt.active[('lobby', 'maya')] = {}
        rt._tool = AsyncMock(side_effect=RuntimeError('lost after effect'))
        unknown = {**params, 'callId': 'unknown'}
        with pytest.raises(RuntimeError):
            await rt.tool('item/tool/call', unknown)
        store.close()
        store = Store(path)
        rt = runtime()
        rt.active[('lobby', 'maya')] = {}
        rt._tool = AsyncMock()
        response = await rt.tool('item/tool/call', unknown)
        assert not response['success'] and 'unconfirmed' in response['contentItems'][0]['text']
        rt._tool.assert_not_awaited()
        assert not (await rt.tool('item/tool/call', {**params, 'callId': 'new-attempt'}))['success']
        rt._tool.assert_not_awaited()
        assert store.rows("SELECT result FROM tool_receipt WHERE call='unknown'") == [{'result': None}]
    finally:
        store.close()
