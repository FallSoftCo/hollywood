"""Real authenticated HTTP/PostgreSQL, synthetic native provenance only.

The fixture's reported state and stopped descriptors are synthetic setup.
Production Global later supplies the separate real-native recovery proof.
"""
import asyncio,json
from edit_plan_fixture import Fixture,uid

async def main():
    f=Fixture();await f.start()
    checks=[]
    try:
        case=await f.case('reported-work-cleanup')
        i=f.agents['i']
        await f.propose(case);await f.ready(case)
        reservation,reserved=await f.reserve(case)
        begun=await f.call(i,body=f.phase(case,reservation,reserved['receipt']['permit'],'begin'))
        held=(await f.call(i,body=f.phase(case,reservation,begun['receipt']['permit'],'settle',outcome='unknown')))['receipt']['permit']
        assert held['state']=='held' and case['counter']==0
        # Explicit synthetic lifecycle setup, not a real model/report claim.
        for member in ('i','p','r','t'):
            f.sql("UPDATE hb_work SET state='blocked',revision=revision+1 WHERE id=%s",(f.agents[member]['work'],))
        f.stop_integrator(case)
        observation=await f.http(i['host']['token'],'POST','collaboration/mutation-observations',{
            'operationId':uid(),'bridgeRevision':i['host']['revision'],'permitId':held['id'],
            'permitRevision':held['revision'],'executorId':reservation['executorId'],
            'descriptorDigest':'d'*64,'serviceBindingDigest':'e'*64,
            'channel':'stopped-executor-observation/v1','outcome':'notApplied',
            'filesObserved':f.observed(case),'note':'SYNTHETIC stopped descriptor; actual untouched fixture bytes'})
        observed=observation['receipt'];assert observed['permit']['state']=='held'
        f.later(i)
        work_revision=3
        await f.reserve(case,status=409,workRevision=work_revision)
        checks.append('Blocked Work cannot obtain a normal new mutation permit')
        await f.consent(case,f.agents['p'],settlement=None,status=409,extra={'workRevision':work_revision})
        await f.consent(case,f.agents['p'],settlement='f'*64,status=409,extra={'workRevision':work_revision})
        checks.append('Ordinary consent and wrong observation digest are refused')
        command=f.command(case,i,'reconcile',workRevision=work_revision,
            permitId=held['id'],permitRevision=observed['permit']['revision'],
            observationDigest=observed['observationDigest'],outcome='notApplied',effectDisposition='reviewed')
        await f.call(i,command,status=409)
        checks.append('Observation alone and old producer votes cannot settle')
        for name in ('p','r','t'):
            await f.consent(case,f.agents[name],settlement=observed['observationDigest'],extra={'workRevision':work_revision})
        await f.consent(case,i,settlement=observed['observationDigest'],extra={'workRevision':work_revision})
        checks.append('Fresh participant and last-integrator consent accepts same original blocked Work without refreshing grants')
        body=f.envelope(i,command)
        settled=await f.call(i,body=body)
        assert settled['admission'] is None and settled['receipt']['permit']['state']=='settled'
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s',(held['id'],))['n']==0
        revision=settled['receipt']['permit']['revision']
        replay=await f.call(i,body=body)
        assert replay['admission'] is None and replay['receipt']==settled['receipt']
        assert f.sql('SELECT revision FROM hb_edit_permit WHERE id=%s',(held['id'],))['revision']==revision
        assert f.sql('SELECT state,revision FROM hb_work WHERE id=%s',(i['work'],))=={'state':'blocked','revision':3}
        assert case['counter']==0
        checks.append('Original all-before permit settles once as metadata, replay returns original receipt; target mutation count zero and Work remains blocked')
        result={'state':'passed','actualAuthenticatedHTTP':True,'actualPostgreSQL':True,
            'nativeProvenance':'SYNTHETIC','nativeProof':False,'modelsStarted':0,
            'groups':checks,'httpRecords':len(f.trace),'targetFixtureMutationCount':case['counter']}
        (f.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:await f.close()

if __name__=='__main__':asyncio.run(main())
