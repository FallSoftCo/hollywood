"""Actual HTTP/PG administrator recovery; native provenance is SYNTHETIC only."""
import asyncio,copy,json
from edit_plan_fixture import Fixture,uid
from hollywood_backbone.store import digest

async def main():
    f=Fixture();await f.start();checks=[]
    try:
        case=await f.case('admin-original-plan-recovery')
        i=f.agents['i'];p=f.agents['p']
        await f.propose(case);await f.ready(case)
        reservation,reserved=await f.reserve(case)
        begun=await f.call(i,body=f.phase(case,reservation,reserved['receipt']['permit'],'begin'))
        held=(await f.call(i,body=f.phase(case,reservation,begun['receipt']['permit'],'settle',outcome='unknown')))['receipt']['permit']
        for a in ('i','p','r','t'):
            f.sql("UPDATE hb_work SET state='blocked',revision=revision+1 WHERE id=%s",(f.agents[a]['work'],))
        f.stop_integrator(case)
        obs=(await f.http(i['host']['token'],'POST','collaboration/mutation-observations',{
            'operationId':uid(),'bridgeRevision':i['host']['revision'],'permitId':held['id'],
            'permitRevision':held['revision'],'executorId':reservation['executorId'],
            'descriptorDigest':'d'*64,'serviceBindingDigest':'e'*64,
            'channel':'stopped-executor-observation/v1','outcome':'notApplied',
            'filesObserved':f.observed(case),'note':'SYNTHETIC stopped setup; actual before-file bytes'}))['receipt']
        f.later(i)
        path='collaboration/mutations/'+held['id']
        view=await f.http(i['token'],'GET',path)
        assert view['scope']=='plan' and view['fileManifest']==case['manifest']['files']
        assert view['mayApply'] is False and view['newExecutionAdmitted'] is False
        checks.append('Common read returns original plan mutation and original manifest without authority')
        # Explicit synthetic role setup. This is not deployment/admin enrollment proof.
        f.sql('UPDATE hb_principal SET administrator=true WHERE id=%s',(i['id'],))
        payload={'operationId':uid(),'permitRevision':obs['permit']['revision'],
                 'observationDigest':obs['observationDigest'],'outcome':'notApplied',
                 'effectDisposition':'reviewed','note':'SYNTHETIC administrator explicitly reviews original file facts'}

        async def native_post(agent,body,status,source=None):
            actual=source or {**agent['source'],'callId':uid(),'tool':'hollywood_mutation_reconcile',
                              'argumentsDigest':digest(['hollywood_mutation_reconcile',body])}
            async with f.client.post(f.url+path+'/reconcile',json=body,headers={
                'Authorization':'Bearer '+agent['token'],
                'X-Hollywood-Native-Source':json.dumps(actual)}) as response:
                result=await response.json()
                f.trace.append({'method':'POST','path':path+'/reconcile','status':response.status,
                                'request':copy.deepcopy(body),'result':result,'nativeProvenance':'SYNTHETIC'})
                with (f.out/'admin-http-trace.jsonl').open('a') as log:
                    log.write(json.dumps(f.trace[-1],default=str)+'\n')
                assert response.status==status,(response.status,status,result)
                return result,actual

        await native_post(p,payload,403)
        await f.http(i['token'],'POST',path+'/reconcile',payload,status=403,attest=False)
        checks.append('Nonadministrator and agent-without-native-provenance cannot reconcile')
        await native_post(i,{**payload,'operationId':uid(),'observationDigest':'f'*64},409)
        f.sql("UPDATE hb_executor SET state='live' WHERE id=%s",(reservation['executorId'],))
        await native_post(i,{**payload,'operationId':uid()},409)
        f.sql("UPDATE hb_executor SET state='stopped' WHERE id=%s",(reservation['executorId'],))
        checks.append('Wrong observation and original executor no longer stopped are refused')
        assert all(v['settlement_digest'] is None for v in (await f.view(case))['consents'])
        accepted,source=await native_post(i,payload,200)
        receipt=accepted['receipt']
        assert accepted['admission'] is None and receipt['permit']['state']=='settled'
        assert receipt['reviewedBy']==i['id'] and receipt['permit']['settlement']['reviewerId']==i['id']
        assert receipt['permit']['settlement']['originalSettlement']['outcome']=='unknown'
        assert receipt['permit']['settlement']['externalEffectsUndone'] is False
        event=f.sql('SELECT e.author,p.kind FROM hb_event e JOIN hb_principal p ON p.id=e.author WHERE e.id=%s',(receipt['eventId'],))
        assert str(event['author'])==i['id'] and event['kind']=='agent'
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s',(held['id'],))['n']==0
        assert f.sql('SELECT state,revision FROM hb_work WHERE id=%s',(i['work'],))=={'state':'blocked','revision':3}
        again,_=await native_post(i,payload,200,source=source)
        assert again==accepted and case['counter']==0
        checks.append('Authenticated agent administrator settles known original bytes; original failure/agent authorship/blocked Work retained; no target edit or new admission')
        result={'state':'passed','actualAuthenticatedHTTP':True,'actualPostgreSQL':True,
            'nativeProvenance':'SYNTHETIC','nativeProof':False,'modelsStarted':0,
            'groups':checks,'httpRecords':len(f.trace),'targetFixtureMutationCount':case['counter']}
        (f.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:await f.close()

if __name__=='__main__':asyncio.run(main())
