import pytest

from hollywood_room.models import Message
from hollywood_room.rooms import catalog, create, join, provision
from hollywood_room.server import Service
from hollywood_room.store import Conflict, Store


def test_explicit_room_owner_default_reply_and_duplicate_routing(tmp_path):
    path = tmp_path / 'room.db'
    store = Store(path)
    try:
        store.add_team('enterprise', 'Enterprise', 'global-steward', 'Coordinate')
        room = create(store, 'enterprise', 'enterprise-lax', 'LAX', 'Native execution')
        service = Service(store, 'token')
        human = service.post(Message(commandId='setup', task='team-enterprise', body='Set up LAX owner'), team='enterprise')
        assert human['data']['targets'] == ['global-steward']
        with pytest.raises(ValueError, match='accountable owner'):
            service.post(Message(commandId='unowned', task='enterprise-lax', body='Do work'), team='enterprise')
        assert store.rows("SELECT * FROM command WHERE id='unowned'") == []
        before = Message(commandId='before-upgrade', task='enterprise-lax', body='Earlier accepted request')
        old = store.message(before.commandId, task=before.task, author='you', body=before.body,
                            targets=['global-steward'], authority='user')
        provision(store, 'team-enterprise', 'global-steward', task='enterprise-lax', agent='lax-owner',
                  role='Own native execution', source=human['id'], request_id='lax-owner')
        assert service.post(before, team='enterprise') == old
        store.add_teammate('peer', 'enterprise', 'Peer')
        actors = store.rows("SELECT id FROM actor_catalog WHERE legacy_agent='lax-owner'")
        assert actors
        with pytest.raises(Conflict):
            provision(store, 'team-enterprise', 'global-steward', task='enterprise-lax', agent='peer',
                      role='Peer', source=human['id'], request_id='conflict')
        catalog(store, 'enterprise')
        join(store, room['id'], 'enterprise', store.rows("SELECT id FROM actor_catalog WHERE legacy_agent='peer'")[0]['id'])
        direct = Message(commandId='direct', task='enterprise-lax', body='Audit lifecycle')
        event = service.post(direct, team='enterprise')
        assert event['data']['targets'] == ['lax-owner']
        assert service.post(direct, team='enterprise') == event
        deliveries = store.rows('SELECT * FROM delivery WHERE event=?', (event['id'],))
        assert len(deliveries) == 1 and deliveries[0]['agent'] == 'lax-owner'
        assert deliveries[0]['authority'] == 'user'
        explicit = service.post(Message(commandId='explicit', task='enterprise-lax', body='Review', targets=['global-steward']), team='enterprise')
        assert explicit['data']['targets'] == ['global-steward']
        summary = store.message('summary', task='team-enterprise', author='lax-owner', body='Material result',
                                targets=['global-steward'], authority='tool')
        assert store.rows('SELECT authority FROM delivery WHERE event=?', (summary['id'],)) == [{'authority': 'tool'}]
        reply = service.post(Message(commandId='reply', replyTo=summary['id'], body='Clarify'), team='enterprise')
        assert reply['data']['targets'] == ['lax-owner']
        store.close()
        store = Store(path)
        assert Service(store, 'token').post(direct, team='enterprise') == event
    finally:
        store.close()
