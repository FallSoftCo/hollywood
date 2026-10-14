from types import SimpleNamespace

import pytest

from hollywood_room.actions import perform
from hollywood_room.models import Message
from hollywood_room.rooms import create
from hollywood_room.server import Service
from hollywood_room.store import Conflict, Store


async def test_owner_question_reply_status_receipts_survive_restart(tmp_path):
    path = tmp_path / 'room.db'
    store = Store(path)
    try:
        store.add_team('enterprise', 'Enterprise', 'global-steward', 'Coordinate')
        store.add_teammate('owner', 'enterprise', 'Implement')
        create(store, 'enterprise', 'owning-room', 'Owner', 'Explicit assignments')
        source = Service(store, 'token').post(Message(commandId='human', task='team-enterprise',
            body='Implement assigned work', targets=['global-steward']), team='enterprise')['id']
        active = {'delivery': {'depth': 0, 'event': source}, 'visibility': 'room'}
        runtime = SimpleNamespace(store=store)
        assignment = {'task': 'owning-room', 'agent': 'owner', 'title': 'Owner', 'brief': 'Implement'}
        assigned = await perform(runtime, ('team-enterprise', 'global-steward'), active,
                                 'hollywood_assign', assignment, 'assign')
        assert await perform(runtime, ('team-enterprise', 'global-steward'), active,
                             'hollywood_assign', assignment, 'assign') == assigned
        assert len(store.rows("SELECT * FROM delivery WHERE agent='owner'")) == 2
        orders = store.rows('SELECT * FROM work_order WHERE event=?', (assigned['event'],))
        assert len(orders) == 1 and orders[0]['owner'] == 'owner' and orders[0]['source'] == source
        held = await perform(runtime, ('team-enterprise', 'global-steward'), active,
                             'hollywood_assign', {**assignment, 'dependencies': [assigned['event']],
                                                  'acceptance': 'Verified native receipt'}, 'dependent-order')
        assert held['admission'] == 'held_dependencies'
        assert store.rows('SELECT * FROM delivery WHERE event=?', (held['event'],)) == []
        assert store.rows('SELECT acceptance FROM work_order WHERE event=?', (held['event'],)) == [
            {'acceptance': 'Verified native receipt'}]
        with pytest.raises(ValueError):
            await perform(runtime, ('team-enterprise', 'global-steward'), active,
                          'hollywood_assign', {**assignment, 'dependencies': [999999]}, 'invalid-order')
        assert store.rows("SELECT * FROM command WHERE id='invalid-order'") == []
        owner = {'delivery': {'depth': 1, 'event': assigned['event']}, 'visibility': 'room'}
        question = await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_ask',
                                 {'question': 'Which acceptance target?'}, 'ask')
        assert await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_ask',
                             {'question': 'Which acceptance target?'}, 'ask') == question
        with pytest.raises(Conflict):
            await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_ask',
                          {'question': 'Different question'}, 'ask')
        status = {'action': 'blocked', 'summary': 'Awaiting acceptance target'}
        await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_task', status, 'blocked')
        await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_task', status, 'blocked')
        notifications = store.rows("SELECT * FROM delivery WHERE agent='global-steward' AND authority='tool'")
        assert notifications == []
        report = await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_say',
            {'task': 'team-enterprise', 'to': 'global-steward', 'response': 'required',
             'body': 'Material report for observation'}, 'report')
        assert report['targets'] == []
        assert store.rows('SELECT * FROM delivery WHERE event=?', (report['published'],)) == []
        brief = await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_brief',
                              {'summary': 'Committed owning-room summary'}, 'brief')
        assert brief['task'] == 'owning-room' and brief['data']['targets'] == []
        store.close()
        store = Store(path)
        runtime.store = store
        assert len(store.rows('SELECT * FROM work_order')) == 2
        assert await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_ask',
                             {'question': 'Which acceptance target?'}, 'ask') == question
        answer = Message(commandId='answer', replyTo=question['attention'], body='Use target A')
        service = Service(store, 'token')
        first = service.post(answer, team='enterprise')
        assert service.post(answer, team='enterprise') == first
        deliveries = store.rows('SELECT * FROM delivery WHERE event=?', (first['id'],))
        assert len(deliveries) == 1
        assert (deliveries[0]['task'], deliveries[0]['agent']) == ('owning-room', 'owner')
        assert store.rows('SELECT state FROM attention WHERE id=?', (question['attention'],)) == [{'state': 'resolved'}]
        assert len(store.rows('SELECT * FROM push_outbox WHERE event=?', (question['attention'],))) == 1
        with pytest.raises(ValueError):
            service.post(Message(commandId='foreign', replyTo=question['attention'], body='Bad scope'))
        # Final status remains in its owning room without a cross-room dispatch.
        await perform(runtime, ('owning-room', 'owner'), owner, 'hollywood_task',
                      {'action': 'completed', 'summary': 'Verified target A'}, 'done')
        assert store.rows("SELECT * FROM delivery WHERE agent='global-steward' AND authority='tool'") == []
        assert all(row['thread'] is None and row['turn'] is None for row in store.rows('SELECT * FROM binding'))
    finally:
        store.close()
