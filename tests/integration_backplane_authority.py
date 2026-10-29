"""Real NATS plus an isolated PostgreSQL schema. No native/model processes."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import tempfile
from uuid import uuid4

import psycopg
from psycopg import sql

from hollywood_backbone.bootstrap import initialize
from hollywood_backbone import native_effects
from hollywood_backbone.store import Store, Denied, Conflict, digest
from hollywood_backbone.backplane import BackplaneError
from hollywood_backbone.backplane_auth import AgentBindings
from hollywood_backbone.backplane_rooms import AgentRoomTransport
from integration_backplane import qualify


async def main(config_path, binary):
    config = json.loads(Path(config_path).read_text())
    schema = 'qa_backplane_' + uuid4().hex
    # No public fallback in the search path: a missing fixture table must fail,
    # never read or mutate the production authority.
    fixture = {**config, 'options': config.get('options', '') + ' -c search_path=' + schema}
    with psycopg.connect(**config, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    store = None
    try:
        with tempfile.TemporaryDirectory(prefix='backplane-authority-') as directory:
            root = Path(directory)
            initialize(fixture, root / 'first', 'Backplane authority qualification')
            initialize(fixture, root / 'second', 'Foreign authority qualification')
            first = json.loads((root / 'first/installation-intent.json').read_text())
            second = json.loads((root / 'second/installation-intent.json').read_text())
            store = Store(fixture)
            bindings = AgentBindings(store)
            bindings.migrate()
            owner = first['credentials']['Owner']['principalId']
            foreign = second['credentials']['Global']['principalId']
            authority = first['authorityId']
            cpu_agent, gpu_agent, private_room, shared_room = [str(uuid4()) for _ in range(4)]
            cpu_token, gpu_token = [secrets.token_urlsafe(40) for _ in range(2)]
            checks = []
            def denied(call):
                try:
                    call()
                except Denied:
                    return
                raise AssertionError('Unauthorized identity accepted')
            def configure(a, b):
                with store.pool.connection() as connection:
                    for agent, token, label, host in [(cpu_agent,cpu_token,'CPU agent',a.host_id),
                                                     (gpu_agent,gpu_token,'GPU agent',b.host_id)]:
                        connection.execute('INSERT INTO hb_principal(id,authority,name,kind) VALUES(%s,%s,%s,\'agent\')',
                                           (agent, authority, label))
                        connection.execute('INSERT INTO hb_token VALUES(%s,%s,false)', (digest(token),agent))
                        connection.execute('INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s)',
                                           (host,authority,label,'qualification-only','{}'))
                        connection.execute('INSERT INTO hb_placement(authority,agent,host) VALUES(%s,%s,%s)',
                                           (authority,agent,host))
                    for room, name in [(shared_room,'Shared qualification room'),(private_room,'Private qualification room')]:
                        connection.execute('INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,%s,%s,%s)',
                                           (room,authority,name,'Fixture only',gpu_agent))
                    for room, agent in [(shared_room,cpu_agent),(shared_room,gpu_agent),(private_room,gpu_agent)]:
                        connection.execute('INSERT INTO hb_membership(authority,room,principal) VALUES(%s,%s,%s)',
                                           (authority,room,agent))
                assert bindings.bind(owner,host_id=a.host_id,principal_id=cpu_agent,token_digest=digest(cpu_token)) == 1
                assert bindings.bind(owner,host_id=b.host_id,principal_id=gpu_agent,token_digest=digest(gpu_token)) == 1
                assert bindings.bind(owner,host_id=a.host_id,principal_id=cpu_agent,token_digest=digest(cpu_token)) == 1
                checks.append('Repeated explicit enrollment retains revision')
                denied(lambda: bindings.bind(cpu_agent,host_id=a.host_id,principal_id=cpu_agent,token_digest=digest(cpu_token)))
                denied(lambda: bindings.bind(owner,host_id=a.host_id,principal_id=owner,
                                              token_digest=digest(first['credentials']['Owner']['token'])))
                checks.append('Nonadministrator enrollment and human identity enrollment refused')
                reads = AgentRoomTransport(bindings)
                b.handlers.update(reads.handlers())
                async def verify():
                    value = await a.request(b.host_id,'hollywood.rooms.list',{'actorId':cpu_agent},request_id=str(uuid4()))
                    assert {room['id'] for room in value['rooms']} == {shared_room}
                    assert cpu_token not in json.dumps(value) and 'token_digest' not in json.dumps(value)
                    checks.append('Actual broker/domain read retains room privacy and excludes credentials')
                    for principal in (gpu_agent, owner, foreign):
                        try:
                            await a.request(b.host_id,'hollywood.rooms.list',{'actorId':principal},request_id=str(uuid4()))
                            raise AssertionError('Spoofed actor accepted')
                        except BackplaneError as error:
                            assert error.code == 'AGENT_ACCESS_DENIED'
                    checks.append('Wrong host, human impersonation and foreign authority denied over NATS')
                    with store.pool.connection() as connection:
                        before = connection.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
                    try:
                        await a.request(b.host_id,'hollywood.message.send',{
                            'actorId':cpu_agent,'roomId':shared_room,
                            'message':{'operationId':'no-native-source','kind':'message','body':'Must not publish','audience':'room'}},request_id=str(uuid4()))
                        raise AssertionError('Machine authentication bypassed native admission')
                    except BackplaneError as error:
                        assert error.code == 'AGENT_ACCESS_DENIED'
                    with store.pool.connection() as connection:
                        assert connection.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n'] == before
                    checks.append('Host credential alone cannot publish; native effect boundary retained with zero events')

                    def resolve():
                        return bindings.resolve(authority_id=authority,authenticated_host_id=a.host_id,principal_id=cpu_agent)
                    with store.pool.connection() as connection:
                        connection.execute('UPDATE hb_token SET revoked=true WHERE digest=%s',(digest(cpu_token),))
                    denied(resolve)
                    with store.pool.connection() as connection:
                        connection.execute('UPDATE hb_token SET revoked=false WHERE digest=%s',(digest(cpu_token),))
                        connection.execute('UPDATE hb_placement SET enabled=false WHERE agent=%s',(cpu_agent,))
                    denied(resolve)
                    with store.pool.connection() as connection:
                        connection.execute('UPDATE hb_placement SET enabled=true,generation=generation+1 WHERE agent=%s',(cpu_agent,))
                    denied(resolve)
                    try:
                        bindings.bind(owner,host_id=a.host_id,principal_id=cpu_agent,token_digest=digest(cpu_token))
                        raise AssertionError('Stale binding replaced without CAS')
                    except Conflict:
                        pass
                    assert bindings.bind(owner,host_id=a.host_id,principal_id=cpu_agent,
                                         token_digest=digest(cpu_token),expected_revision=1) == 2
                    assert resolve().placement_generation == 2
                    stale_admission = resolve()
                    assert bindings.revoke(owner,host_id=a.host_id,principal_id=cpu_agent,expected_revision=2) == 3
                    with native_effects.transport(cpu_agent,stale_admission.token_digest,
                            'GET','catalog','rooms',None,None,admission=stale_admission):
                        denied(lambda: store.catalog(cpu_agent))
                    checks.append('Revocation between authentication and domain execution rejects stale admission')
                    denied(resolve)
                    checks.append('Credential revocation, placement disable/movement and binding revocation honored')
                    checks.append('Placement-generation replacement requires explicit current revision')
                    return {'passed':True,'checks':checks,'realPostgreSQL':True,'isolatedSchema':True,
                            'modelsOrWorkersStarted':False}
                return verify
            result = await qualify(binary,authority_id=authority,configure=configure)
            print(json.dumps(result,indent=2))
    finally:
        if store:
            store.close()
        with psycopg.connect(**config,autocommit=True) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    import sys
    asyncio.run(main(sys.argv[1],sys.argv[2]))
