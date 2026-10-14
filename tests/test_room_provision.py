from types import SimpleNamespace

import pytest

from hollywood_room.actions import perform
from hollywood_room.store import Conflict, Store


async def test_provision_authority_receipts_and_no_execution(tmp_path):
    path = tmp_path / 'room.db'
    store = Store(path)
    try:
        source = store.message('human', task='lobby', author='you', body='Set up the room owner',
                               targets=[], authority='user')['id']
        store.add_team('foreign', 'Foreign', 'foreign-coordinator', 'Coordinator')
        peer = store.message('peer', task='lobby', author='maya', body='Set up an owner',
                             targets=[], authority='tool')['id']
        runtime = SimpleNamespace(store=store)
        active = {'delivery': {'depth': 0}, 'visibility': 'room'}
        args = {'task': 'lobby', 'agent': 'room-owner', 'role': 'Own room implementation',
                    'source': source, 'request_id': 'setup-owner'}

        async def call(**changes):
            return await perform(runtime, ('lobby', 'coordinator'), active,
                                 'hollywood_provision', {**args, **changes}, 'transport')

        with pytest.raises(ValueError):
            await perform(runtime, ('lobby', 'maya'), active, 'hollywood_provision', args, 'peer')
        for changes in [{'source': peer}, {'source': 9999}, {'task': 'team-foreign'},
                        {'agent': 'you'}, {'role': ' '}]:
            with pytest.raises(ValueError):
                await call(**changes)
        with pytest.raises(Conflict):
            await call(agent='foreign-coordinator')
        with pytest.raises(ValueError):
            await perform(runtime, ('lobby', 'coordinator'), {**active, 'visibility': 'direct:coordinator'},
                          'hollywood_provision', args, 'private')
        assert store.rows("SELECT * FROM event WHERE kind='provision'") == []
        receipt = await call()
        assert await call() == receipt
        assert not receipt['executionAdmitted']
        assert len(store.rows("SELECT * FROM event WHERE kind='provision'")) == 1
        with pytest.raises(Conflict):
            await call(role='Changed role')
        assert store.rows('SELECT * FROM binding') == []
        assert store.rows('SELECT * FROM delivery') == []
        assert store.rows('SELECT * FROM push_outbox') == []
        store.close()
        store = Store(path)
        runtime.store = store
        assert await call() == receipt
        assert store.rows('SELECT * FROM binding') == []
        assert store.rows('SELECT * FROM delivery') == []
    finally:
        store.close()
