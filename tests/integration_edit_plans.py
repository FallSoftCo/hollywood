"""Fresh SQL/HTTP edit-plan lifecycle; native/controller provenance is SYNTHETIC.

The fixture mutates its own RAM bytes only after a first-response begin grant.
This is not native execute_verified_patch or a genuine protected-bridge proof.
"""
import asyncio
import copy
import json

from edit_plan_fixture import Fixture, uid
from hollywood_backbone.store import digest


async def main():
    f = Fixture()
    await f.start()
    i, p, r, t, outsider = (f.agents[n] for n in ('i', 'p', 'r', 't', 'outsider'))
    original_attempts = f.sql('SELECT * FROM hb_work_attempt ORDER BY delivery', many=True)
    original_contexts = f.sql('SELECT * FROM hb_agent_context ORDER BY principal', many=True)
    original_bindings = f.sql('SELECT * FROM hb_agent_execution ORDER BY delivery', many=True)
    deliveries_before = f.sql('SELECT count(*) AS n FROM hb_delivery')['n']
    from physical_target_checks import qualify
    checks = []

    async def ready(case):
        await f.propose(case)
        await f.ready(case)

    async def begin(case, reservation, response):
        body = f.phase(case, reservation, response['receipt']['permit'], 'begin')
        return body, await f.call(case['integrator'], body=body)

    async def settle(case, reservation, response, outcome='applied'):
        body = f.phase(case, reservation, response['receipt']['permit'], 'settle', outcome=outcome)
        return await f.call(case['integrator'], body=body)

    async def arbitrary_status(agent, body):
        f.attest(body)
        async with f.client.post(f.url + 'collaboration/edit-plans', json=body,
                                headers={'Authorization': 'Bearer ' + agent['host']['token']}) as response:
            value = await response.json()
            row = {'request': copy.deepcopy(body), 'status': response.status, 'result': value}
            f.race.append(row)
            with (f.out / 'race-trace.jsonl').open('a') as output:
                output.write(json.dumps(row) + '\n')
            return row

    try:
        checks.extend(await qualify(f))
        policy = await f.http(i['host']['token'], 'GET', f"hosts/{i['host']['id']}/collaboration-bridge")
        assert policy['editPlans'] and policy['patchPermits'] and policy['nativeChildAdmission'] is False
        case = await f.case('positive')
        body = f.envelope(i, f.command(case, i, 'propose', planRevision=0, manifest=case['manifest']))
        await f.call(i, body=body, token=i['token'], status=403)
        await f.call(i, body={**body, 'hostId': i['host']['id']}, status=400)
        proposed = await f.call(i, body=body)
        assert proposed['admission'] is None and proposed['receipt']['mayApply'] is False
        assert (await f.call(i, body=body))['receipt'] == proposed['receipt']
        modified = copy.deepcopy(body)
        modified['command']['note'] = 'Changed operation'
        assert (await f.call(i, body=modified, status=409))['error'] == 'operation_changed'
        invalid = await f.case('invalid')
        invalid['manifest']['slices'][1]['byteStart'] = 1
        invalid['manifest']['slices'][1]['byteEnd'] = 4
        bad = f.command(invalid, i, 'propose', planRevision=0, manifest=invalid['manifest'])
        assert (await f.call(i, bad, status=409))['error'] == 'slice_conflict'
        invalid['manifest']['slices'][1]['byteStart'] = 3
        invalid['manifest']['slices'][1]['byteEnd'] = 5
        bad_claim = invalid['manifest']['files'][0]['claimId']
        f.sql('UPDATE hb_path_claim SET owner=%s WHERE id=%s', (outsider['id'], bad_claim))
        assert (await f.call(i, f.command(invalid, i, 'propose', planRevision=0, manifest=invalid['manifest']), status=409))['error'] == 'claim_changed'
        f.sql('UPDATE hb_path_claim SET owner=%s WHERE id=%s', (p['id'], bad_claim))
        await f.call(outsider, f.command(case, outsider, 'consent', consentRevision=0,
            manifestSha256=proposed['receipt']['manifestSha256'], sourceRevision='a' * 40,
            artifacts=[], imports=[], participantConsentDigest=None, settlementDigest=None), status=409)
        checks.append('Protected enrolled service and actual current work scope; strict fields; immutable operation replay; revision/content-bound nonoverlapping slices')

        await f.consent(case, i, status=409)
        await asyncio.gather(f.consent(case, p), f.consent(case, r), f.consent(case, t))
        await f.consent(case, i, status=409, extra={'imports': []})
        await f.consent(case, i)
        state = await f.view(case)
        assert state['plan']['state'] == 'ready' and len(state['consents']) == 4
        imports = next(v for v in state['consents'] if v['work'] == i['work'])['imports']
        assert len(imports) == 2  # Different host with same UUID, and same host/different workspace.
        checks.append('Explicit producer and last integrator consent; foreign host/workspace artifact imports must match actual RAM bytes and canonical slice hashes')

        await f.reserve(case, status=409, patchSha256='f' * 64)
        await f.reserve(case, status=409, sourceRevision='f' * 40)
        observed = f.observed(case)
        observed[1]['sha256'] = 'f' * 64
        await f.reserve(case, status=409, filesObserved=observed)
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock')['n'] == 0
        reservation, reserved = await f.reserve(case)
        assert reserved['admission'] is None and reserved['receipt']['permit']['state'] == 'reserved'
        begin_body, begun = await begin(case, reservation, reserved)
        assert begun['admission']['mayApply'] and begun['receipt']['mayApply'] is False
        replay = await f.call(i, body=begin_body)
        assert replay['admission'] is None and replay['receipt'] == begun['receipt']
        recovered = await f.http(i['host']['token'], 'GET', f"collaboration/commands/{i['id']}/{begin_body['operationId']}")
        assert recovered['acceptedResult']['mayApply'] is False and 'admission' not in recovered['acceptedResult']
        duplicate_begin = copy.deepcopy(begin_body)
        duplicate_begin['operationId'] = uid()
        await f.call(i, body=duplicate_begin, status=409)
        f.apply_bytes(case)
        settled = await settle(case, reservation, begun)
        assert settled['receipt']['permit']['state'] == 'settled' and case['counter'] == 1
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock')['n'] == 0
        for file in case['manifest']['files']:
            claim = f.sql('SELECT * FROM hb_path_claim WHERE id=%s', (file['claimId'],))
            assert claim['content_sha256'] == file['afterSha256'] and claim['revision'] == 2
        checks.append('Atomic all-file before/source/patch check; one-shot original begin response; retry/receipt cannot replay; actual fixture byte counter=1 and known after hashes settle/update claims')

        no_effect = await f.case('known-not-applied')
        await ready(no_effect)
        no_source, no_reserved = await f.reserve(no_effect)
        _, no_begun = await begin(no_effect, no_source, no_reserved)
        unmodified = await settle(no_effect, no_source, no_begun, 'notApplied')
        assert unmodified['receipt']['permit']['state'] == 'settled' and no_effect['counter'] == 0
        await f.reserve(no_effect, status=409)
        checks.append('Explicit known not-applied settlement checks every before byte, releases locks and still requires a new revision/consent before another attempt')

        drift = await f.case('after-content-drift')
        await ready(drift)
        drift_source, drift_reserved = await f.reserve(drift)
        _, drift_begun = await begin(drift, drift_source, drift_reserved)
        (i['root'] / drift['manifest']['files'][0]['relativePath']).write_bytes(b'unexpected concurrent content')
        drift_result = await settle(drift, drift_source, drift_begun)
        assert drift_result['receipt']['permit']['state'] == 'held'
        assert 'content_drift' in drift_result['receipt']['permit']['settlement']['holds']
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s', (drift_begun['receipt']['permit']['id'],))['n'] == 2
        checks.append('Unexpected actual RAM after-content is preserved as held evidence, never accepted as the declared patch result')

        expired_execution = await f.case('execution-expired')
        await ready(expired_execution)
        late_source, late_reserved = await f.reserve(expired_execution)
        _, late_begun = await begin(expired_execution, late_source, late_reserved)
        f.apply_bytes(expired_execution)
        f.sql("UPDATE hb_edit_permit SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (late_begun['receipt']['permit']['id'],))
        late = await settle(expired_execution, late_source, late_begun)
        assert late['receipt']['permit']['state'] == 'held' and 'permit_expired' in late['receipt']['permit']['settlement']['holds']
        checks.append('Expired executing ownership cannot become success even when declared after hashes match; both locks remain held for explicit reconciliation')

        # New plan revisions require every new consent, with no inherited grant.
        revised = copy.deepcopy(case['manifest'])
        for file in revised['files']:
            file.update(beforeSha256=file['afterSha256'], beforeSize=file['afterSize'], claimRevision=2)
        for item in revised['slices']:
            item['beforeSha256'] = next(x for x in revised['files'] if x['relativePath'] == item['relativePath'])['beforeSha256']
        await f.call(i, f.command(case, i, 'revise', manifest=revised))
        case['revision'], case['manifest'] = 2, revised
        state = await f.view(case)
        assert state['consents'] == [] and state['plan']['state'] == 'collecting'
        await f.reserve(case, status=409)
        await f.call(i, f.command(case, i, 'withdraw'))
        checks.append('Append-only manifest revision invalidates all consent and cannot reuse a settled mutation; withdrawal grants no editing authority')

        same = await f.case('same-operation-race')
        await ready(same)
        state = await f.view(same)
        command = f.command(same, i, 'preflight', permitId=uid(), manifestSha256=state['manifestSha256'],
            consentSetDigest=state['consentSetDigest'], patchSha256=same['manifest']['patchSha256'],
            sourceRevision='a' * 40, filesObserved=f.observed(same), leaseSeconds=30)
        envelope = f.envelope(i, command)
        results = await asyncio.gather(f.call(i, body=envelope), f.call(i, body=envelope))
        assert results[0]['receipt'] == results[1]['receipt'] and all(x['admission'] is None for x in results)
        begin_body = f.phase(same, envelope, results[0]['receipt']['permit'], 'begin')
        grants = await asyncio.gather(f.call(i, body=begin_body), f.call(i, body=begin_body))
        assert sum(bool(g['admission']) for g in grants) == 1
        granted = next(g for g in grants if g['admission'])
        f.apply_bytes(same)
        assert (await settle(same, envelope, granted))['receipt']['permit']['state'] == 'settled'
        checks.append('Actual PostgreSQL same-operation race creates one reservation and exactly one transient begin grant')

        first = await f.case('overlap-first')
        second = await f.case('overlap-second')
        shared = copy.deepcopy(first['manifest']['files'][0])
        old_path = second['manifest']['files'][0]['relativePath']
        second['manifest']['files'][0] = shared
        for item in second['manifest']['slices']:
            if item['relativePath'] == old_path:
                item['relativePath'] = shared['relativePath']
        await ready(first)
        await ready(second)
        state1, state2 = await f.view(first), await f.view(second)
        bodies = []
        for item, state in ((first, state1), (second, state2)):
            bodies.append(f.envelope(i, f.command(item, i, 'preflight', permitId=uid(),
                manifestSha256=state['manifestSha256'], consentSetDigest=state['consentSetDigest'],
                patchSha256=item['manifest']['patchSha256'], sourceRevision='a' * 40,
                filesObserved=f.observed(item), leaseSeconds=30)))
        races = await asyncio.gather(*(arbitrary_status(i, b) for b in bodies))
        assert sorted(row['status'] for row in races) == [200, 409]
        loser = races.index(next(row for row in races if row['status'] == 409))
        loser_id = bodies[loser]['command']['permitId']
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s', (loser_id,))['n'] == 0
        checks.append('Actual overlapping multi-file preflight race has one winner and zero partial locks for loser')

        revoked = await f.case('revoked-consent')
        await ready(revoked)
        rev_source, rev_reservation = await f.reserve(revoked)
        state = await f.view(revoked)
        vote = next(v for v in state['consents'] if v['work'] == p['work'])
        await f.call(p, f.command(revoked, p, 'revoke', consentRevision=vote['revision']))
        await f.call(i, body=f.phase(revoked, rev_source, rev_reservation['receipt']['permit'], 'begin'), status=409)
        held = (await f.view(revoked))['permits'][0]
        assert held['state'] == 'held' and f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s', (held['id'],))['n'] == 2
        await f.call(i, f.command(revoked, i, 'revise', manifest=revoked['manifest']), status=409)
        claim = revoked['manifest']['files'][0]
        raw_claim = f.envelope(p, {'action': 'release'})
        raw_claim['source']['tool'] = 'coordination_act'
        raw_claim['command'] = {'action': 'release', 'workId': p['work'], 'workRevision': 2,
            'claimId': claim['claimId'], 'claimRevision': 1, 'briefingSequence': 0,
            'note': 'SYNTHETIC premature release', 'effectDisposition': 'reviewed'}
        await f.http(p['host']['token'], 'POST', 'collaboration/path-claims', raw_claim, 409)
        checks.append('Revoked participant consent holds all paths, denies begin/revise and freezes underlying claim release/handoff metadata')

        expired = await f.case('expired')
        await ready(expired)
        exp_source, exp_reservation = await f.reserve(expired)
        f.sql("UPDATE hb_edit_permit SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (exp_reservation['receipt']['permit']['id'],))
        await f.call(i, body=f.phase(expired, exp_source, exp_reservation['receipt']['permit'], 'begin'), status=409)
        assert (await f.view(expired))['permits'][0]['effectiveState'] == 'held'
        await f.reserve(expired, status=409)
        checks.append('Expired reservation remains held; time and original receipt never license a new attempt')

        authority = await f.case('authority')
        await ready(authority)
        auth_source, auth_reservation = await f.reserve(authority)
        for table, where in [('hb_physical_grant', ('workspace', i['workspace'])), ('hb_physical_workspace', ('id', i['workspace']))]:
            column, identity = where
            f.sql(f"UPDATE {table} SET state='revoked' WHERE {column}=%s", (identity,))
            await f.call(i, body=f.phase(authority, auth_source, auth_reservation['receipt']['permit'], 'begin'), status=409)
            assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s', (auth_reservation['receipt']['permit']['id'],))['n'] == 2
            f.sql(f"UPDATE {table} SET state='active' WHERE {column}=%s", (identity,))
        checks.append('Current physical root and per-work grant revocation at begin deny mutation and retain the whole all-file reservation')
        f.sql('UPDATE hb_membership SET can_post=false WHERE room=%s AND principal=%s', (f.ids['room'], p['id']))
        await f.call(i, body=f.phase(authority, auth_source, auth_reservation['receipt']['permit'], 'begin'), status=403)
        assert (await f.view(authority))['permits'][0]['effectiveState'] == 'held'
        f.sql('UPDATE hb_membership SET can_post=true WHERE room=%s AND principal=%s', (f.ids['room'], p['id']))
        f.sql("UPDATE hb_work SET state='cancel_requested' WHERE id=%s", (p['work'],))
        await f.call(i, body=f.phase(authority, auth_source, auth_reservation['receipt']['permit'], 'begin'), status=409)
        f.sql("UPDATE hb_work SET state='assigned' WHERE id=%s", (p['work'],))
        f.sql('UPDATE hb_collaboration_bridge SET state=%s WHERE host=%s', ('revoked', r['host']['id']))
        await f.call(i, body=f.phase(authority, auth_source, auth_reservation['receipt']['permit'], 'begin'), status=409)
        f.sql('UPDATE hb_collaboration_bridge SET state=%s WHERE host=%s', ('active', r['host']['id']))
        checks.append('Current participant posting rights, cancellation and bridge enrollment revocation are rechecked at actual begin, not inferred from cached consent')

        other_namespace = await f.case('different-namespace', integrator='t')
        await ready(other_namespace)
        ns_source, ns_reserved = await f.reserve(other_namespace)
        assert ns_reserved['receipt']['permit']['proof']['workspaceId'] == t['workspace']
        wrong = f.phase(other_namespace, ns_source, ns_reserved['receipt']['permit'], 'begin')
        wrong['source'] = {**i['source'], 'tool': 'apply_patch'}
        wrong['executorId'] = i['executor']
        await f.call(i, body=wrong, status=403)
        checks.append('Different physical host/workspace roots remain separate; integrator/permit cannot move to a same-authority peer namespace')

        unknown = await f.case('unknown-after-apply')
        await ready(unknown)
        unk_source, unk_reserved = await f.reserve(unknown)
        _, unk_begun = await begin(unknown, unk_source, unk_reserved)
        f.apply_bytes(unknown)
        held_reply = await settle(unknown, unk_source, unk_begun, 'unknown')
        held = held_reply['receipt']['permit']
        assert held['state'] == 'held' and unknown['counter'] == 1
        assert f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s', (held['id'],))['n'] == 2
        premature = {'operationId': uid(), 'bridgeRevision': i['host']['revision'], 'permitId': held['id'],
            'permitRevision': held['revision'], 'executorId': unk_source['executorId'],
            'descriptorDigest': 'd' * 64, 'serviceBindingDigest': 'e' * 64,
            'channel': 'stopped-executor-observation/v1', 'outcome': 'applied',
            'filesObserved': f.observed(unknown), 'note': 'SYNTHETIC independent after-byte observation'}
        await f.http(i['host']['token'], 'POST', 'collaboration/mutation-observations', premature, 409)
        stopped = f.stop_integrator(unknown)
        observed_reply = await f.http(i['host']['token'], 'POST', 'collaboration/mutation-observations', premature)
        evidence = observed_reply['receipt']
        assert evidence['evidenceOnly'] and observed_reply['admission'] is None
        assert (await f.http(i['host']['token'], 'POST', 'collaboration/mutation-observations', premature))['receipt'] == evidence
        checks.append('Unknown result after actual fixture write holds both files; only independently stopped original executor can record exact after-byte observation; observation never applies or unlocks')

        f.later(i)
        reconciliation = f.command(unknown, i, 'reconcile', permitId=held['id'],
            permitRevision=evidence['permit']['revision'], observationDigest=evidence['observationDigest'],
            outcome='applied', effectDisposition='reviewed')
        await f.call(i, reconciliation, status=409)
        await f.ready(unknown, settlement=evidence['observationDigest'])
        reconciled = await f.call(i, reconciliation)
        assert reconciled['receipt']['permit']['state'] == 'settled' and reconciled['admission'] is None
        assert unknown['counter'] == 1 and f.sql('SELECT count(*) AS n FROM hb_edit_file_lock WHERE permit=%s', (held['id'],))['n'] == 0
        await f.reserve(unknown, status=409)
        checks.append('Later same-context evidence reconciliation requires fresh exact-observation consent from every producer and integrator last; closes known bytes without replay or new work')

        assert f.sql('SELECT * FROM hb_work_attempt ORDER BY delivery', many=True) == original_attempts
        assert f.sql('SELECT * FROM hb_agent_context ORDER BY principal', many=True) == original_contexts
        bindings = f.sql('SELECT * FROM hb_agent_execution ORDER BY delivery', many=True)
        assert [b for b in bindings if str(b['delivery']) != i['source']['deliveryId']] == original_bindings
        assert f.sql('SELECT count(*) AS n FROM hb_delivery')['n'] == deliveries_before + 1
        assert f.sql('SELECT count(*) AS n FROM hb_work_report_reconciliation')['n'] == 0
        checks.append('All original work attempts/context/bindings preserved; one explicit synthetic later request only; zero work reports/acceptance/new native admissions from editing metadata')
        audit = {'schema': 'synthetic-canonical-collaboration-evidence/v1', 'nativeFacts': 'SYNTHETIC ONLY',
            'tables': {table: f.sql('SELECT * FROM ' + table + ' ORDER BY 1', many=True) for table in (
                'hb_command', 'hb_native_effect', 'hb_activity', 'hb_path_claim', 'hb_edit_plan',
                'hb_edit_plan_revision', 'hb_edit_consent', 'hb_edit_permit', 'hb_edit_file_lock',
                'hb_physical_workspace', 'hb_physical_grant')}}
        (f.out / 'canonical-audit.json').write_text(json.dumps(audit, indent=2, default=str) + '\n')
        result = {'state': 'passed', 'groups': checks, 'actualPostgreSQL': True, 'actualAuthenticatedHTTP': True,
            'nativeLifecycle': 'SYNTHETIC ONLY', 'nativeProof': False, 'httpRecords': len(f.trace), 'raceRecords': len(f.race),
            'positiveFixtureMutationCount': case['counter'], 'unknownFixtureMutationCount': unknown['counter']}
        (f.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({**result, 'groups': len(checks)}))
    finally:
        await f.close()


if __name__ == '__main__':
    asyncio.run(main())
