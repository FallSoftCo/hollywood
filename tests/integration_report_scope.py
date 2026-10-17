"""Narrow real PG/authenticated HTTP cross-room scope recovery; native facts SYNTHETIC."""
import asyncio
import copy
import json
from pathlib import Path
from psycopg.types.json import Jsonb
from report_scope_fixture import Fixture, uid
from hollywood_backbone.report_scope_models import ReportScopeBriefing, ReportScopeProof, ReportReconciliationReceipt
from hollywood_backbone.store import digest, json_value


async def main():
    f=Fixture();await f.start()
    a,p=(f.agents[n] for n in ('worker','peer'))
    original={}
    for agent in (a,p):
        original[agent['id']]={
            'delivery':f.sql('SELECT * FROM hb_delivery WHERE id=%s',(agent['prior'],)),
            'result':f.sql('SELECT e.* FROM hb_event e JOIN hb_delivery d ON d.result_event=e.id WHERE d.id=%s',(agent['prior'],)),
            'attempt':f.sql('SELECT * FROM hb_work_attempt WHERE delivery=%s',(agent['prior'],)),
            'effects':f.sql('SELECT * FROM hb_native_effect WHERE delivery=%s ORDER BY sequence',(agent['prior'],),many=True),
            'executor':f.sql('SELECT * FROM hb_executor WHERE delivery=%s',(agent['prior'],))}
    identities={t:f.sql('SELECT * FROM '+t+' ORDER BY 1',many=True) for t in ('hb_agent_context','hb_agent_execution')}
    active={agent['id']:f.sql('SELECT * FROM hb_delivery WHERE id=%s',(agent['source']['deliveryId'],)) for agent in (a,p)}
    count=f.sql('SELECT count(*) AS n FROM hb_delivery')['n']
    checks=[]
    try:
        d1=await f.decision(a,'A','requirement-one')
        await f.decision(a,'A','requirement-two')
        parent=await f.decision(a,'global','goal-requirement',goal=f.ids['goal'])
        await f.decision(a,'B','current-b-only')
        b=await f.api(a,'GET',f"connector/deliveries/{a['source']['deliveryId']}/briefing")
        assert b['roomId']==f.ids['B'] and all(d['room']==f.ids['B'] for d in b['decisions'])
        bare=f.report_body(a,None,briefingSequence=b['asOfSequence']);bare.pop('scopeRead')
        assert (await f.report(a,bare,status=400))['error']=='invalid_scope_request'
        fake={'schema':'completed-report-scope-proof/v1','readId':uid(),'revision':1,
            'snapshotSha256':'f'*64,'scopeSha256':'e'*64,'decisionsSha256':'d'*64,'complete':True}
        assert (await f.report(a,f.report_body(a,fake,briefingSequence=b['asOfSequence']),status=409))['error']=='scope_read_missing'
        checks.append('Fresh actual room-B briefing/global scalar and caller-invented proof cannot replace an original room-A scope read')

        await f.read(a,{**f.read_body(a),'priorDeliveryId':p['prior']},status=409)
        await f.api(a,'POST',f"work/{p['work']}/report-briefing",f.read_body(p),f.source(a,'hollywood_briefing'),403)
        await f.read(a,{**f.read_body(a),'roomId':f.ids['B']},status=400)
        await f.read(a,source=f.source(a,'hollywood_work_report_reconcile'),status=403)
        await f.api(a,'POST',f"work/{a['work']}/report-briefing",f.read_body(a),status=403)
        checks.append('Wrong original delivery/work/room selector, foreign owner and unscoped/wrong-tool transport hold before issuing evidence')

        first_body=f.read_body(a,pageSize=1);first_source=f.source(a,'hollywood_briefing')
        first=await f.read(a,first_body,first_source)
        ReportScopeBriefing.model_validate(first)
        assert first['scope']['roomId']==f.ids['A'] and first['scope']['goalRoomId']==f.ids['global']
        assert first['reader']['deliveryId']==a['source']['deliveryId'] and first['reader']['requestId']==a['source']['requestId']
        assert first['scope']['deliveryId']==a['prior'] and first['decisionCount']==3 and first['scopeRead'] is None
        assert first['complete'] is False and first['nextCursor']==1
        assert await f.read(a,first_body,first_source)==first
        assert f.sql('SELECT covered_count FROM hb_report_scope_read WHERE id=%s',(first['readId'],))['covered_count']==1
        forged={**fake,'readId':first['readId'],'revision':first['revision'],
            **{k:first[k] for k in ('snapshotSha256','scopeSha256','decisionsSha256')}}
        assert (await f.report(a,f.report_body(a,forged),status=409))['error']=='scope_read_incomplete'
        await f.read(a,f.read_body(a,readId=first['readId'],cursor=2,pageSize=1),status=409)
        second=await f.read(a,f.read_body(a,readId=first['readId'],cursor=1,pageSize=1))
        final=await f.read(a,f.read_body(a,readId=first['readId'],cursor=2,pageSize=1))
        assert second['scopeRead'] is None and final['complete'] and final['coveredCount']==3
        ReportScopeProof.model_validate(final['scopeRead'])
        served=first['decisions']+second['decisions']+final['decisions']
        assert digest(served)==final['decisionsSha256'] and all(d['roomId']!=f.ids['B'] for d in served)
        assert any(d['roomId']==f.ids['global'] and d['goalId']==f.ids['goal'] for d in served)
        checks.append('Ordered bounded pages cover exact original work-room and parent-goal scopes; partial/skip/forged-complete evidence denied and same-operation replay never advances coverage')

        await f.decision(a,'A','requirement-one',revision=1,identity=d1)
        assert (await f.report(a,f.report_body(a,final['scopeRead']),status=409))['error']=='scope_decisions_changed'
        await f.read(a,first_body,first_source,status=409)
        current=await f.read(a)
        assert next(d for d in current['decisions'] if d['id']==d1)['revision']==2
        foreign=await f.read(p)
        assert (await f.report(a,f.report_body(a,foreign['scopeRead']),status=409))['error']=='scope_read_missing'
        for key in ('scopeSha256','snapshotSha256','decisionsSha256'):
            wrong={**current['scopeRead'],key:'f'*64}
            assert (await f.report(a,f.report_body(a,wrong),status=409))['error']=='scope_proof_changed'
        await f.report(a,{**f.report_body(a,current['scopeRead']),'priorDeliveryId':p['prior']},status=409)
        f.sql("UPDATE hb_report_scope_read SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(current['readId'],))
        assert (await f.report(a,f.report_body(a,current['scopeRead']),status=409))['error']=='scope_expired'
        current=await f.read(a)
        checks.append('Changed A decision invalidates old complete evidence despite newer global head; foreign proof, altered scope/revision hashes, wrong original IDs and expired reads hold')

        for room in ('A','B'):
            f.sql('UPDATE hb_membership SET can_post=false WHERE principal=%s AND room=%s',(a['id'],f.ids[room]))
            await f.report(a,f.report_body(a,current['scopeRead']),status=403)
            await f.read(a,status=403)
            f.sql('UPDATE hb_membership SET can_post=true WHERE principal=%s AND room=%s',(a['id'],f.ids[room]))
        f.sql('UPDATE hb_membership SET can_read=false,can_post=false,can_request=false WHERE principal=%s AND room=%s',(a['id'],f.ids['A']))
        await f.report(a,f.report_body(a,current['scopeRead']),status=403)
        f.sql('UPDATE hb_membership SET can_read=true,can_post=true,can_request=true WHERE principal=%s AND room=%s',(a['id'],f.ids['A']))
        f.sql("UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s",(a['source']['deliveryId'],))
        await f.report(a,f.report_body(a,current['scopeRead']),status=409)
        f.sql("UPDATE hb_delivery SET cancellation_requested=false WHERE id=%s",(a['source']['deliveryId'],))
        for table,identity in (('hb_work',a['work']),('hb_goal',f.ids['goal'])):
            f.sql(f"UPDATE {table} SET state='cancel_requested' WHERE id=%s",(identity,))
            await f.report(a,f.report_body(a,current['scopeRead']),status=409)
            f.sql(f"UPDATE {table} SET state=%s WHERE id=%s",('assigned' if table=='hb_work' else 'active',identity))
        f.sql('UPDATE hb_work SET owner=%s WHERE id=%s',(p['id'],a['work']))
        await f.report(a,f.report_body(a,current['scopeRead']),status=403)
        f.sql('UPDATE hb_work SET owner=%s WHERE id=%s',(a['id'],a['work']))
        for key,val in (('contextId','f'*64),('roleRevision',999),('generation',2),('claimId','wrong-current-claim')):
            await f.report(a,f.report_body(a,current['scopeRead']),{**f.source(a,'hollywood_work_report_reconcile'),key:val},409)
        assert f.sql('SELECT count(*) AS n FROM hb_work_report_reconciliation')['n']==0
        checks.append('Present original/current read/post, owner, cancellation, role, private context and native generation/claim fences remain mandatory after proof issuance; denied calls create no report')

        conversation=await f.api(a,'GET',f"rooms/{f.ids['A']}/history?view=conversation&limit=100")
        assert not any(e['data'].get('type')=='reportScopeProtocol' for e in conversation['events'])
        audit_view=await f.api(a,'GET',f"rooms/{f.ids['A']}/history?view=all&limit=100")
        own_reads=[e for e in audit_view['events'] if e['data'].get('type')=='reportScopeProtocol' and e['author']==a['id']]
        assert own_reads and all(e['projection']['kind']=='protocolAudit' and e['audience']=='direct' and e['recipient']==a['id'] for e in own_reads)
        peer_view=await f.api(p,'GET',f"rooms/{f.ids['A']}/history?view=all&limit=100")
        assert not any(e['id'] in {r['id'] for r in own_reads} for e in peer_view['events'])
        assert f.sql('SELECT count(*) AS n FROM hb_activity')['n']==0
        checks.append('Scope pages are immutable private audit/command evidence, absent from conversation and foreign-member audit; no native lifecycle/action/card is fabricated')

        await f.decision(a,'global','goal-requirement',revision=1,identity=parent,goal=f.ids['goal'],state='conflicted')
        conflicted=await f.read(a)
        assert any(d['id']==parent and d['state']=='conflicted' for d in conflicted['decisions'])
        await f.report(a,f.report_body(a,conflicted['scopeRead']),status=409)
        assert f.sql('SELECT count(*) AS n FROM hb_work_report_reconciliation')['n']==0
        await f.decision(a,'global','goal-requirement',revision=2,identity=parent,goal=f.ids['goal'])
        current=await f.read(a)
        checks.append('A complete fresh snapshot containing a conflicted parent-goal decision cannot authorize submitted evidence; unresolved conflicts cover the same exact verified scopes')

        submitted_body=f.report_body(a,current['scopeRead'],briefingSequence=b['asOfSequence'])
        submitted_source=f.source(a,'hollywood_work_report_reconcile')
        report=await f.report(a,submitted_body,submitted_source)
        ReportReconciliationReceipt.model_validate(report)
        assert report['state']=='submitted' and report['deliveryId']==a['prior']
        assert report['reconciliation']['briefingSequence']==current['originalRoomDecisionSequence']
        assert report['reconciliation']['callerBriefingSequence']==b['asOfSequence']
        assert report['reconciliation']['scopeVerification']['proof']==current['scopeRead']
        assert report['reconciliation']['originalBriefingSequence']==0
        assert f.sql('SELECT state FROM hb_work WHERE id=%s',(a['work'],))['state']=='submitted'
        assert await f.report(a,submitted_body,submitted_source)==report
        await f.report(a,{**submitted_body,'operationId':uid()},status=409)
        f.sql('UPDATE hb_membership SET can_post=false WHERE principal=%s AND room=%s',(a['id'],f.ids['A']))
        await f.report(a,submitted_body,submitted_source,status=403)
        f.sql('UPDATE hb_membership SET can_post=true WHERE principal=%s AND room=%s',(a['id'],f.ids['A']))
        recovered=await f.api(a,'GET','commands/'+submitted_body['operationId'])
        assert recovered['canonicalCommand']['acceptedResult']==report
        attempt_before_review=f.sql('SELECT * FROM hb_work_attempt WHERE delivery=%s',(a['prior'],))
        assert {k:v for k,v in attempt_before_review.items() if k not in ('report_event','evidence','report_note')}=={
            k:v for k,v in original[a['id']]['attempt'].items() if k not in ('report_event','evidence','report_note')}
        checks.append('Exact current scoped evidence creates only the original-lineage submitted report; caller scalar is diagnostic, replay/GET preserve original receipt and do not rerun or accept work')

        await f.api('owner','POST',f"work/{a['work']}/review", {'operationId':uid(),'revision':3,'decision':'accepted','note':'SYNTHETIC distinct reviewer inspected original artifact and scoped report'})
        assert f.sql('SELECT state FROM hb_work WHERE id=%s',(a['work'],))['state']=='accepted'
        peer_scope=await f.read(p)
        peer_report=await f.report(p,f.report_body(p,peer_scope['scopeRead']))
        legacy=copy.deepcopy(peer_report['reconciliation']);legacy.pop('scopeVerification')
        legacy['schema']='completed-work-report-reconciliation/v1'
        f.sql('UPDATE hb_work_report_reconciliation SET evidence=%s WHERE delivery=%s',(Jsonb(legacy),p['prior']))
        await f.api('owner','POST',f"work/{p['work']}/review", {'operationId':uid(),'revision':3,'decision':'accepted',
            'note':'SYNTHETIC legacy unscoped receipt cannot become new acceptance'},status=409)
        f.sql('UPDATE hb_work_report_reconciliation SET evidence=%s WHERE delivery=%s',(Jsonb(peer_report['reconciliation']),p['prior']))
        await f.decision(a,'global','goal-requirement',revision=3,identity=parent,goal=f.ids['goal'])
        await f.api('owner','POST',f"work/{p['work']}/review", {'operationId':uid(),'revision':3,'decision':'accepted','note':'SYNTHETIC parent-goal decision changed after report'},status=409)
        assert f.sql('SELECT state FROM hb_work WHERE id=%s',(p['work'],))['state']=='submitted'
        checks.append('Independent review is separate; exact relevant parent-goal revision changes after submission also fence later acceptance')

        assert f.sql('SELECT count(*) AS n FROM hb_delivery')['n']==count
        for table,rows in identities.items():assert f.sql('SELECT * FROM '+table+' ORDER BY 1',many=True)==rows
        for agent in (a,p):
            before=original[agent['id']]
            assert f.sql('SELECT * FROM hb_delivery WHERE id=%s',(agent['prior'],))==before['delivery']
            assert f.sql('SELECT e.* FROM hb_event e JOIN hb_delivery d ON d.result_event=e.id WHERE d.id=%s',(agent['prior'],))==before['result']
            assert f.sql('SELECT * FROM hb_native_effect WHERE delivery=%s ORDER BY sequence',(agent['prior'],),many=True)==before['effects']
            assert f.sql('SELECT * FROM hb_executor WHERE delivery=%s',(agent['prior'],))==before['executor']
            after=f.sql('SELECT * FROM hb_work_attempt WHERE delivery=%s',(agent['prior'],))
            additive=('report_event','evidence','report_note','review_event','review_decision','review_note')
            assert {k:v for k,v in after.items() if k not in additive}=={k:v for k,v in before['attempt'].items() if k not in additive}
            assert after['review_decision']==('accepted' if agent is a else None)
            assert f.sql('SELECT * FROM hb_delivery WHERE id=%s',(agent['source']['deliveryId'],))==active[agent['id']]
            assert json.loads((f.out/'workspaces'/agent['workspace']/'result.json').read_text())['counter']==1
        checks.append('Original attempt identity/specification/briefing fields, completion/results/effects/resources, private contexts/bindings and active room-B participation remain unchanged; only additive report/review fields change, actual RAM counters stay1 and zero deliveries are admitted')
        evidence={'nativeFacts':'SYNTHETIC ONLY','originalBaseline':original,'privateIdentityBaseline':identities,
            'currentParticipationBaseline':active,'tables':{t:f.sql('SELECT * FROM '+t+' ORDER BY 1',many=True) for t in (
                'hb_report_scope_read','hb_work_report_reconciliation','hb_command','hb_native_effect','hb_work_attempt','hb_agent_context','hb_agent_execution','hb_activity')}}
        (f.out/'canonical-audit.json').write_text(json.dumps(json_value(evidence),indent=2)+'\n')
        result={'state':'passed','groups':checks,'actualPostgreSQL':True,'actualAuthenticatedHTTP':True,
            'nativeFacts':'SYNTHETIC ONLY','nativeProof':False,'httpRecords':len(f.trace),'raceRecords':0,
            'originalCounters':[1,1],'newDeliveryCount':0,'currentParticipationUnchanged':True,'independentReviewSeparate':True}
        (f.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({**result,'groups':len(checks)}))
    finally:
        await f.close()


if __name__=='__main__':asyncio.run(main())
