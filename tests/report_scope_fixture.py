"""Fresh synthetic lifecycle, actual isolated SQL/HTTP and owned RAM artifacts."""
import copy
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession, web
from psycopg.types.json import Jsonb
from hollywood_backbone.native_effects import HEADER
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest, json_value


def uid():
    return str(uuid4())


class Fixture:
    def __init__(self):
        self.out = Path(os.environ['CANONICAL_TEST_OUTPUT'])
        config = json.loads(Path(os.environ['CANONICAL_TEST_DATABASE']).read_text())
        assert str(self.out).startswith('/dev/shm/') and config['host'].startswith('/dev/shm/')
        self.store = Store(config)
        self.store.migrate()
        self.ids = {n: uid() for n in ('authority','global','A','B','goal','host','controller')}
        self.credentials = {n: {'principalId': uid(), 'token': secrets.token_urlsafe(40)} for n in ('Owner','Global')}
        self.owner = self.credentials['Owner']['principalId']
        self.store.bootstrap(self.ids['authority'], 'SYNTHETIC original-report scope', {
            'globalRoomId': self.ids['global'], 'credentials': self.credentials})
        self.store.principal(self.owner, self.ids['controller'], 'SYNTHETIC controller', 'service', secrets.token_urlsafe(40))
        self.agents = {n: {'id':uid(), 'work':uid(), 'workspace':uid(), 'token':secrets.token_urlsafe(40)} for n in ('worker','peer')}
        for name, agent in self.agents.items():
            self.store.principal(self.owner, agent['id'], 'SYNTHETIC ' + name, 'agent', agent['token'])
            self.store.placement(self.owner, self.ids['host'], 'SYNTHETIC host', 'synthetic-host-proof', {}, agent['id'])
        for room in ('A','B'):
            self.store.create_room(self.owner, self.ids[room], 'SYNTHETIC room ' + room,
                                   'Original A / current B fixture', self.agents['worker']['id'])
        for agent in self.agents.values():
            for room in ('global','A','B'):
                old = self.sql('SELECT revision FROM hb_membership WHERE room=%s AND principal=%s', (self.ids[room],agent['id']))
                self.store.membership(self.owner,self.ids[room],agent['id'], {'can_read':True,'can_post':True,'can_request':True},old['revision'] if old else 0)
        self.sql('''INSERT INTO hb_host_admission(host,authority,controller,machine_identity,max_concurrent,
            reserved_interactive,memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
            VALUES(%s,%s,%s,'SYNTHETIC',8,0,1,0,300,300)''', (self.ids['host'], self.ids['authority'], self.ids['controller']))
        goal_source = self.store.post(self.owner,self.ids['global'],uid(), {'kind':'request','audience':'room',
            'recipientId':self.credentials['Global']['principalId'],'body':'SYNTHETIC scope fixture source; no real human instruction'})
        self.goal_source = goal_source['event']['id']
        created = self.store.workflow(self.owner,'goal.create',self.ids['global'],uid(), {'id':self.ids['goal'],
            'sourceEventId':self.goal_source,'title':'SYNTHETIC recovery scope','objective':'Only substrate qualification',
            'acceptance':'Independent review remains separate','reviewBudget':0})
        self.store.workflow(self.owner,'goal.cooperation',self.ids['goal'],uid(), {'revision':created['goalRevision'],
            'cooperationRevision':0,'policy':'leader_award','executorIds':[a['id'] for a in self.agents.values()],'reviewerId':None})
        for agent in self.agents.values():
            revision = self.sql('SELECT revision FROM hb_goal WHERE id=%s',(self.ids['goal'],))['revision']
            self.store.workflow(self.owner,'goal.work',self.ids['goal'],uid(), {'id':agent['work'],'revision':revision,
                'roomId':self.ids['A'],'ownerId':agent['id'],'specification':'SYNTHETIC original A work',
                'acceptance':'Original evidence and separate review','dependencies':[]})
            assigned = self.store.workflow(self.owner,'work.assign',agent['work'],uid(), {'revision':1})
            self.bind(agent, assigned['deliveryId'], assigned['requestEventId'])
            agent['prior_source'] = copy.deepcopy(agent['source'])
            agent['prior'] = assigned['deliveryId']
            agent['request'] = assigned['requestEventId']
            with self.store.pool.connection() as c:
                actor = self.store.actor(c,agent['id'])
                result = self.store.event(c,actor,self.ids['A'],'result','SYNTHETIC original completed without report',
                    source=agent['request'],data={'type':'executionState','deliveryId':agent['prior'],'generation':1,'state':'completed'})
            self.sql("UPDATE hb_delivery SET state='completed',result_event=%s WHERE id=%s",(result,agent['prior']))
            self.sql("UPDATE hb_executor SET state='stopped',evidence=%s WHERE delivery=%s",
                (Jsonb({'syntheticFixtureOnly':True,'reason':'registered-unit-drained'}),agent['prior']))
            self.sql('UPDATE hb_work_attempt SET briefing_sequence=0 WHERE delivery=%s',(agent['prior'],))
            root = self.out / 'workspaces' / agent['workspace']
            root.mkdir(parents=True)
            artifact = root/'result.json'
            artifact.write_text('{"counter":1}\n')
            import hashlib
            agent['evidence'] = [{'hostId':self.ids['host'],'workspaceId':agent['workspace'],
                'relativePath':'result.json','sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),'size':artifact.stat().st_size}]
            self.sql('''INSERT INTO hb_native_effect(authority,principal,delivery,generation,fingerprint,source,
                method,path,wire_digest,operation) VALUES(%s,%s,%s,1,%s,%s,'POST','SYNTHETIC-original-effect',%s,%s)''',
                (self.ids['authority'],agent['id'],agent['prior'],digest(uid()),Jsonb({'syntheticFixtureOnly':True}),digest(artifact.read_text()),uid()))
            later = self.store.post(self.owner,self.ids['B'],uid(), {'kind':'request','audience':'room',
                'recipientId':agent['id'],'body':'SYNTHETIC later ordinary B participation; do not repeat A effects'})
            self.bind(agent,later['deliveries'][0]['id'],later['event']['id'])
        self.trace=[]

    def sql(self,statement,args=(),many=False):
        with self.store.pool.connection() as c:
            result=c.execute(statement,args)
            if result.description:
                return result.fetchall() if many else result.fetchone()

    def bind(self,a,delivery,request):
        context,thread=digest(a['id']),'SYNTHETIC-thread-'+a['id']
        claim,turn='SYNTHETIC-claim-'+delivery,'SYNTHETIC-turn-'+delivery
        self.sql("UPDATE hb_delivery SET state='running',host=%s,generation=1,claim_id=%s,native_thread=%s,native_turn=%s WHERE id=%s",
            (self.ids['host'],claim,thread,turn,delivery))
        if not self.sql('SELECT 1 FROM hb_agent_context WHERE principal=%s',(a['id'],)):
            self.sql('INSERT INTO hb_agent_context(principal,authority,context_id,native_thread,host,workspace_id) VALUES(%s,%s,%s,%s,%s,%s)',
                (a['id'],self.ids['authority'],context,thread,self.ids['host'],a['workspace']))
        role=self.store.role_read(self.owner,a['id'])['revision']
        self.sql('INSERT INTO hb_agent_execution VALUES(%s,1,%s,%s,%s,%s,%s,%s)',(delivery,a['id'],context,thread,a['workspace'],role,digest(delivery)))
        self.sql('''INSERT INTO hb_executor(id,authority,delivery,agent,host,generation,claim_id,descriptor,
            descriptor_digest,state,controller,evidence,observed_at) VALUES(%s,%s,%s,%s,%s,1,%s,'{}',%s,'live',%s,%s,clock_timestamp())''',
            (uid(),self.ids['authority'],delivery,a['id'],self.ids['host'],claim,digest(delivery),self.ids['controller'],Jsonb({'syntheticFixtureOnly':True})))
        a['source']={'schema':'hollywood-native-source/v2','authorityId':self.ids['authority'],'agentId':a['id'],
            'hostId':self.ids['host'],'deliveryId':delivery,'generation':1,'claimId':claim,'requestId':request,
            'nativeThread':thread,'nativeTurn':turn,'contextId':context,'roleRevision':role,'nativeGoalId':None,
            'callId':uid(),'tool':'hollywood_briefing','argumentsDigest':'a'*64}

    async def start(self):
        self.runner=web.AppRunner(application(self.store));await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v3/'
        self.client=ClientSession()

    async def close(self):
        await self.client.close();await self.runner.cleanup();self.store.close()

    def source(self,a,tool):
        return {**a['source'],'tool':tool,'callId':uid()}

    async def api(self,a,method,path,body=None,source=None,status=200):
        token=self.credentials['Owner']['token'] if a=='owner' else a['token']
        headers={'Authorization':'Bearer '+token}
        if source: headers[HEADER]=json.dumps(source)
        async with self.client.request(method,self.url+path,json=body,headers=headers) as response:
            value=await response.json()
            row={'method':method,'path':path,'request':body,'nativeSource':source,'status':response.status,'result':value}
            self.trace.append(copy.deepcopy(row))
            with (self.out/'http-trace.jsonl').open('a') as out:out.write(json.dumps(row,default=str)+'\n')
            assert response.status==status,(path,status,response.status,value)
            return value

    def read_body(self,a,**changes):
        return {'schema':'completed-report-scope-read/v1','operationId':uid(),'revision':2,
            'priorDeliveryId':a['prior'],'readId':None,'cursor':0,'pageSize':32,**changes}

    async def read(self,a,body=None,source=None,status=200):
        return await self.api(a,'POST',f"work/{a['work']}/report-briefing",body or self.read_body(a),
                              source or self.source(a,'hollywood_briefing'),status)

    def report_body(self,a,proof,**changes):
        return {'operationId':uid(),'revision':2,'priorDeliveryId':a['prior'],'scopeRead':proof,'state':'submitted',
            'note':'SYNTHETIC original effects reviewed; no replay','effectDisposition':'reviewed','evidence':a['evidence'],**changes}

    async def report(self,a,body,source=None,status=200):
        return await self.api(a,'POST',f"work/{a['work']}/report-reconcile",body,
                             source or self.source(a,'hollywood_work_report_reconcile'),status)

    async def decision(self,a,room,key,revision=0,identity=None,goal=None):
        source=self.goal_source if room=='global' else a['request'] if room=='A' else a['source']['requestId']
        body={'operationId':uid(),'id':identity or uid(),'key':key,'revision':revision,'goalId':goal,
            'state':'active','body':'SYNTHETIC scoped requirement '+str(revision),'sourceEventId':source}
        await self.api('owner','POST',f"rooms/{self.ids[room]}/decisions",body)
        return body['id']
