"""Actual PostgreSQL/HTTP fixture with explicitly synthetic native provenance.

No bridge/controller/native/model starts. RAM files exercise digest bindings;
host/workspace IDs simulate namespaces and are not a true multi-host proof.
"""
import copy
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from psycopg.types.json import Jsonb

from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest


def uid():
    return str(uuid4())


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self):
        self.out = Path(os.environ['CANONICAL_TEST_OUTPUT'])
        config = json.loads(Path(os.environ['CANONICAL_TEST_DATABASE']).read_text())
        assert str(self.out).startswith('/dev/shm/') and config['host'].startswith('/dev/shm/')
        self.store = Store(config)
        self.store.migrate()
        self.ids = {k: uid() for k in ('authority', 'room', 'goal', 'host1', 'host2', 'workspace1', 'workspace2')}
        self.credentials = {name: {'principalId': uid(), 'token': secrets.token_urlsafe(40)} for name in ('Owner', 'Global')}
        self.owner = self.credentials['Owner']['principalId']
        self.store.bootstrap(self.ids['authority'], 'SYNTHETIC edit-plan/permit qualification', {
            'globalRoomId': self.ids['room'], 'credentials': self.credentials})
        self.hosts = {}
        for name in ('host1', 'host2'):
            host = {'id': self.ids[name], 'controller': uid(), 'token': secrets.token_urlsafe(40), 'revision': 1}
            self.store.principal(self.owner, host['controller'], 'SYNTHETIC controller ' + name, 'service', host['token'])
            self.hosts[name] = host
        self.agents = {}
        for name in ('i', 'p', 'r', 't', 'outsider'):
            host = self.hosts['host2' if name == 'r' else 'host1']
            agent = {'id': uid(), 'token': secrets.token_urlsafe(40), 'work': uid(), 'name': name,
                     'host': host, 'workspace': self.ids['workspace2' if name == 't' else 'workspace1']}
            agent['root'] = self.out / 'namespaces' / host['id'] / agent['workspace']
            agent['root'].mkdir(parents=True, exist_ok=True)
            self.store.principal(self.owner, agent['id'], 'SYNTHETIC worker ' + name, 'agent', agent['token'])
            self.store.membership(self.owner, self.ids['room'], agent['id'], {'can_read': True, 'can_post': True, 'can_request': True}, 0)
            self.store.placement(self.owner, host['id'], 'SYNTHETIC ' + host['id'], 'synthetic-identity-proof', {}, agent['id'])
            if not self.sql('SELECT 1 FROM hb_host_admission WHERE host=%s', (host['id'],)):
                self.sql("""INSERT INTO hb_host_admission(host,authority,controller,machine_identity,
                    max_concurrent,reserved_interactive,memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
                    VALUES(%s,%s,%s,'synthetic',16,0,1,0,300,300)""", (host['id'], self.ids['authority'], host['controller']))
            self.agents[name] = agent
        source = self.store.post(self.owner, self.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': self.credentials['Global']['principalId'], 'body': 'SYNTHETIC local fixture; no real human source.'})
        goal = self.store.workflow(self.owner, 'goal.create', self.ids['room'], uid(), {'id': self.ids['goal'],
            'sourceEventId': source['event']['id'], 'title': 'SYNTHETIC edit plans', 'objective': 'Substrate only',
            'acceptance': 'No native proof', 'reviewBudget': 0})
        self.store.workflow(self.owner, 'goal.cooperation', self.ids['goal'], uid(), {'revision': goal['goalRevision'],
            'cooperationRevision': 0, 'policy': 'leader_award', 'executorIds': [a['id'] for a in self.agents.values()], 'reviewerId': None})
        for agent in self.agents.values():
            self.store.workflow(self.owner, 'goal.work', self.ids['goal'], uid(), {'id': agent['work'],
                'revision': self.sql('SELECT revision FROM hb_goal WHERE id=%s', (self.ids['goal'],))['revision'],
                'roomId': self.ids['room'], 'ownerId': agent['id'], 'specification': 'SYNTHETIC editing fixture',
                'acceptance': 'Actual SQL/HTTP invariants', 'dependencies': []})
            assigned = self.store.workflow(self.owner, 'work.assign', agent['work'], uid(), {'revision': 1})
            self.bind(agent, assigned['deliveryId'], assigned['requestEventId'])
        self.trace, self.race = [], []

    def sql(self, query, args=(), many=False):
        with self.store.pool.connection() as c:
            result = c.execute(query, args)
            if result.description:
                return result.fetchall() if many else result.fetchone()

    def bind(self, agent, delivery, request):
        context, thread = digest(agent['id']), 'synthetic-thread-' + agent['id']
        claim, turn = 'synthetic-claim-' + delivery, 'synthetic-turn-' + delivery
        self.sql("UPDATE hb_delivery SET state='running',host=%s,generation=1,claim_id=%s,native_thread=%s,native_turn=%s WHERE id=%s",
                 (agent['host']['id'], claim, thread, turn, delivery))
        if not self.sql('SELECT 1 FROM hb_agent_context WHERE principal=%s', (agent['id'],)):
            self.sql('INSERT INTO hb_agent_context(principal,authority,context_id,native_thread,host,workspace_id) VALUES(%s,%s,%s,%s,%s,%s)',
                     (agent['id'], self.ids['authority'], context, thread, agent['host']['id'], agent['workspace']))
        self.sql('INSERT INTO hb_agent_execution VALUES(%s,1,%s,%s,%s,%s,1,%s)',
                 (delivery, agent['id'], context, thread, agent['workspace'], digest(delivery)))
        agent['executor'] = uid()
        self.sql("""INSERT INTO hb_executor(id,authority,delivery,agent,host,generation,claim_id,descriptor,
            descriptor_digest,state,controller,evidence,observed_at) VALUES(%s,%s,%s,%s,%s,1,%s,'{}',%s,'live',%s,%s,clock_timestamp())""",
            (agent['executor'], self.ids['authority'], delivery, agent['id'], agent['host']['id'], claim, 'd' * 64,
             agent['host']['controller'], Jsonb({'syntheticFixtureOnly': True, 'reason': 'matched-live-incarnation', 'serviceBindingDigest': 'e' * 64})))
        agent['source'] = {'schema': 'hollywood-native-source/v2', 'authorityId': self.ids['authority'], 'agentId': agent['id'],
            'hostId': agent['host']['id'], 'deliveryId': delivery, 'claimId': claim, 'generation': 1,
            'requestId': request, 'nativeThread': thread, 'nativeTurn': turn, 'callId': uid(), 'tool': 'collaborative_edit_plan',
            'argumentsDigest': 'a' * 64, 'roleRevision': 1, 'contextId': context, 'nativeGoalId': None}

    async def start(self):
        self.runner = web.AppRunner(application(self.store))
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v3/'
        self.client = ClientSession()
        for host in self.hosts.values():
            await self.http(self.credentials['Owner']['token'], 'PUT', f"hosts/{host['id']}/collaboration-bridge", {
                'operationId': uid(), 'revision': 0, 'controllerId': host['controller'], 'state': 'active',
                'protocol': 'canonical-edit-plans/v1', 'adapterSha256': 'a' * 64, 'enrollmentSha256': 'b' * 64})

    async def close(self):
        await self.client.close()
        await self.runner.cleanup()
        self.store.close()

    async def http(self, token, method, path, body=None, status=200):
        async with self.client.request(method, self.url + path, json=body, headers={'Authorization': 'Bearer ' + token}) as response:
            value = await response.json()
            row = {'method': method, 'path': path, 'status': response.status, 'result': value}
            self.trace.append(row)
            with (self.out / 'http-trace.jsonl').open('a') as output:
                output.write(json.dumps(row, default=str) + '\n')
            assert response.status == status, (method, path, response.status, status, value)
            return value

    def envelope(self, agent, command, source=None):
        return {'operationId': uid(), 'bridgeRevision': agent['host']['revision'],
            'source': source or {**agent['source'], 'callId': uid(),
                'tool': 'apply_patch' if command['action'] in ('preflight', 'begin', 'settle') else 'collaborative_edit_plan'},
            'executorId': agent['executor'], 'descriptorDigest': 'd' * 64, 'serviceBindingDigest': 'e' * 64,
            'channel': 'captured-app-server-rpc/v1', 'command': command}

    def command(self, case, agent, action, **fields):
        return {'action': action, 'planId': case['id'], 'planRevision': case.get('revision', 1),
            'workId': agent['work'], 'workRevision': 2, 'briefingSequence': 0,
            'note': 'SYNTHETIC lifecycle qualification', **fields}

    async def call(self, agent, command=None, body=None, status=200, token=None):
        return await self.http(token or agent['host']['token'], 'POST', 'collaboration/edit-plans',
                               body or self.envelope(agent, command), status)

    async def view(self, case):
        return await self.http(case['integrator']['host']['token'], 'GET', 'collaboration/edit-plans/' + case['id'])

    async def case(self, name, integrator='i'):
        target = self.agents[integrator]
        files = []
        for suffix, before, after, claimant in [('a.txt', b'abcdefghij\n', b'ABcDEfGhij\n', self.agents['p']),
                                                ('b.txt', b'abcdefghij\n', b'Xbcdefghij\n', target)]:
            if claimant['host']['id'] != target['host']['id'] or claimant['workspace'] != target['workspace']:
                claimant = target
            path = name + '/' + suffix
            for member in self.agents.values():
                actual = member['root'] / path
                actual.parent.mkdir(parents=True, exist_ok=True)
                actual.write_bytes(before)
            root_call = self.envelope(claimant, {'action': 'acquire'})
            root_call['source']['tool'] = 'coordination_act'
            root_call['command'] = {'action': 'acquire', 'workId': claimant['work'], 'workRevision': 2,
                'claimId': uid(), 'claimRevision': 0, 'briefingSequence': 0, 'note': 'SYNTHETIC target claim',
                'relativePath': path, 'kind': 'file', 'sourceRevision': 'a' * 40, 'contentSha256': sha(before), 'leaseSeconds': 3600}
            claim = await self.http(claimant['host']['token'], 'POST', 'collaboration/path-claims', root_call)
            files.append({'relativePath': path, 'claimId': claim['claim']['id'], 'claimRevision': 1,
                          'beforeSha256': sha(before), 'beforeSize': len(before), 'afterSha256': sha(after), 'afterSize': len(after)})
        slices, artifacts = [], {}
        for name_, file, start, end in [('p', files[0], 0, 2), ('r', files[0], 3, 5),
                                       ('t', files[0], 6, 7), ('i', files[1], 0, 1)]:
            member = self.agents[name_]
            data = ('SYNTHETIC slice:' + name + ':' + name_).encode()
            relative = name + '/artifact-' + name_ + '.patch'
            (member['root'] / relative).write_bytes(data)
            item = {'sliceId': uid(), 'workId': member['work'], 'relativePath': file['relativePath'],
                'beforeSha256': file['beforeSha256'], 'byteStart': start, 'byteEnd': end,
                'hunkSha256': sha(data), 'artifactSha256': sha(data), 'artifactSize': len(data)}
            slices.append(item)
            artifacts[member['work']] = [{'sliceId': item['sliceId'], 'relativePath': relative, 'sha256': sha(data), 'size': len(data)}]
        manifest = {'logicalSource': 'SYNTHETIC repository ' + name, 'sourceRevision': 'a' * 40,
            'integratorWorkId': target['work'], 'participants': [{'workId': self.agents[n]['work'], 'workRevision': 2,
                'sourceRevision': 'a' * 40 if (self.agents[n]['host']['id'], self.agents[n]['workspace']) ==
                    (target['host']['id'], target['workspace']) else 'b' * 40} for n in ('i', 'p', 'r', 't')],
            'files': files, 'slices': slices, 'patchSha256': sha(('SYNTHETIC combined patch:' + name).encode())}
        return {'id': uid(), 'revision': 1, 'integrator': target, 'manifest': manifest, 'artifacts': artifacts, 'counter': 0}

    async def propose(self, case):
        return await self.call(case['integrator'], self.command(case, case['integrator'], 'propose', planRevision=0, manifest=case['manifest']))

    async def consent(self, case, agent, settlement=None, status=200, extra=None):
        state = await self.view(case)
        old = next((v for v in state['consents'] if v['work'] == agent['work']), None)
        imports = []
        if agent['work'] == case['integrator']['work']:
            for peer in self.agents.values():
                if peer['work'] not in case['artifacts'] or peer['work'] == agent['work']:
                    continue
                if (peer['host']['id'], peer['workspace']) != (agent['host']['id'], agent['workspace']):
                    for item in case['artifacts'][peer['work']]:
                        target = agent['root'] / ('imports/' + item['sliceId'] + '.patch')
                        target.parent.mkdir(exist_ok=True)
                        target.write_bytes((peer['root'] / item['relativePath']).read_bytes())
                        imports.append({**item, 'relativePath': str(target.relative_to(agent['root']))})
        member = next(p for p in case['manifest']['participants'] if p['workId'] == agent['work'])
        command = self.command(case, agent, 'consent', consentRevision=old['revision'] if old else 0,
            manifestSha256=state['manifestSha256'], sourceRevision=member['sourceRevision'],
            artifacts=case['artifacts'][agent['work']], imports=imports,
            participantConsentDigest=state['participantConsentDigest'] if agent['work'] == case['integrator']['work'] else None,
            settlementDigest=settlement)
        command.update(extra or {})
        return await self.call(agent, command, status=status)

    async def ready(self, case, settlement=None):
        for name in ('i', 'p', 'r', 't'):
            member = self.agents[name]
            if member['work'] != case['integrator']['work']:
                await self.consent(case, member, settlement)
        await self.consent(case, case['integrator'], settlement)

    def observed(self, case, after=False):
        return [{'relativePath': f['relativePath'], 'sha256': sha((case['integrator']['root'] / f['relativePath']).read_bytes()),
                 'size': (case['integrator']['root'] / f['relativePath']).stat().st_size} for f in case['manifest']['files']]

    async def reserve(self, case, status=200, **extra):
        state = await self.view(case)
        command = self.command(case, case['integrator'], 'preflight', permitId=uid(),
            manifestSha256=state['manifestSha256'], consentSetDigest=state['consentSetDigest'],
            patchSha256=case['manifest']['patchSha256'], sourceRevision=case['manifest']['sourceRevision'],
            filesObserved=self.observed(case), leaseSeconds=30)
        command.update(extra)
        body = self.envelope(case['integrator'], command)
        return body, await self.call(case['integrator'], body=body, status=status)

    def phase(self, case, reservation, permit, action, **extra):
        command = self.command(case, case['integrator'], action, permitId=permit['id'], permitRevision=permit['revision'],
            patchSha256=case['manifest']['patchSha256'], sourceRevision=case['manifest']['sourceRevision'],
            filesObserved=self.observed(case))
        command.update(extra)
        return self.envelope(case['integrator'], command, source=copy.deepcopy(reservation['source']))

    def apply_bytes(self, case):
        assert case['counter'] == 0
        for index, file in enumerate(case['manifest']['files']):
            (case['integrator']['root'] / file['relativePath']).write_bytes(b'ABcDEfGhij\n' if index == 0 else b'Xbcdefghij\n')
        case['counter'] += 1

    def stop_integrator(self, case):
        member = case['integrator']
        original = {'source': copy.deepcopy(member['source']), 'executor': member['executor']}
        self.sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (member['source']['deliveryId'],))
        self.sql("""UPDATE hb_executor SET state='stopped',observed_at=clock_timestamp(),
            evidence=jsonb_set(evidence,'{reason}','"registered-unit-drained"') WHERE id=%s""", (member['executor'],))
        return original

    def later(self, agent):
        request = self.store.post(self.owner, self.ids['room'], uid(), {'kind': 'request', 'audience': 'room',
            'recipientId': agent['id'], 'body': 'SYNTHETIC explicit later evidence reconciliation, no work reassignment.'})
        self.bind(agent, request['deliveries'][0]['id'], request['event']['id'])
