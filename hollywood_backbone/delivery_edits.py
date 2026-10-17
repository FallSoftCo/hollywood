"""Ordinary delivery-scoped one-use permits in the existing physical lock domain."""
from datetime import timedelta
import json

from psycopg.types.json import Jsonb

from . import agent_resources, collaboration as claims, work_edit_authority
from .delivery_edit_models import CAPABILITY, DeliveryEditCall
from .edit_plan_scope import need, now, observed, observed_scope, record
from .edit_plans import original_command
from .store import Conflict, Denied, digest, json_value

PATH = 'collaboration/delivery-edits'
TARGET_KEYS = ('workspaceId', 'workspaceRevision', 'grantRevision', 'environmentSha256',
               'rootIdentitySha256', 'resolution')


def files(c, permit):
    need(permit['scope_kind'] == 'delivery', 'scope_mismatch', 'Ordinary original permit required')
    return permit['proof']['files']


def block_collaboration(c, namespace, paths):
    # No age-based release, and no special exemption for the same agent's work.
    for path in paths:
        conflict = c.execute('''SELECT id FROM hb_path_claim WHERE authority=%s AND host=%s
            AND workspace_id=%s AND state<>'released' AND (relative_path=%s
            OR (kind='directory' AND left(%s,length(relative_path)+1)=relative_path||'/')
            OR left(relative_path,length(%s)+1)=%s||'/') LIMIT 1''',
            (*namespace, path, path, path, path)).fetchone()
        need(not conflict, 'scope_busy', 'Unreleased file/directory work ownership blocks direct editing')
        # Collecting plans are proposals. Ready is explicit coordinated scope;
        # claim release alone does not withdraw a ready plan. Retired metadata
        # cannot grant or block execution independently of claims/permit locks.
        plan = c.execute('''SELECT p.id FROM hb_edit_plan p JOIN hb_edit_plan_revision r
            ON r.plan=p.id AND r.revision=p.revision
            CROSS JOIN LATERAL jsonb_array_elements(r.manifest->'files') f
            WHERE p.authority=%s AND p.host=%s AND p.workspace_id=%s AND p.state='ready'
            AND (f->>'relativePath'=%s OR left(f->>'relativePath',length(%s)+1)=%s||'/'
              OR left(%s,length(f->>'relativePath')+1)=(f->>'relativePath')||'/') LIMIT 1''',
            (*namespace, path, path, path, path)).fetchone()
        need(not plan, 'scope_busy', 'Ready coordinated file scope requires planned editing or explicit withdrawal')


def original_fences(permit, relay, source, binding, payload):
    need(permit['source'] == source and str(permit['delivery']) == source['deliveryId']
         and permit['bridge'] == relay['id'] and permit['bridge_revision'] == payload['bridgeRevision'],
         'source_changed', 'Exact original protected callback and bridge required')
    proof = permit['proof']
    need(all(proof[k] == payload[k] for k in ('executorId', 'descriptorDigest', 'serviceBindingDigest'))
         and all(proof['target'][k] == payload['target'][k] for k in TARGET_KEYS)
         and proof['contextWorkspaceId'] == str(binding['workspace_id']),
         'source_changed', 'Original executor/context/physical authority changed')
    need(proof['patchSha256'] == payload['command']['patchSha256']
         and proof['sourceRevision'] == payload['command']['sourceRevision'],
         'patch_changed', 'Original patch/source evidence changed')


def call(store, actor_id, payload):
    payload = DeliveryEditCall.model_validate(payload).model_dump(mode='json', by_alias=True)
    need(len(json.dumps(payload).encode()) <= 16000, 'request_too_large', 'Bounded ordinary edit metadata required')
    command = payload['command']
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)
        actor, source, binding = claims.participation(store, c, relay, payload,
            tools={'apply_patch'}, patch_actions={'preflight', 'begin', 'settle'})
        request = agent_resources.delivery(store, c, actor, source)
        authority = work_edit_authority.current(store, c, actor, source, request, binding)
        claims.settled_controls(c, {'origin_delivery': source['deliveryId']})
        claims.decisions(c, {'room': request['room'], 'goal': request['data'].get('goalId'),
                            'authority': actor['authority']}, command)
        from .physical_workspaces import attest
        binding = attest(c, relay, source, binding, payload, ordinary=True)
        namespace = claims.namespace_lock(c, actor['authority'], binding['physical_host'], binding['physical_workspace_id'])
        need(command['note'].strip(), 'invalid_request', 'Specific note required')
        old = original_command(c, actor, payload, PATH)
        if old:
            work_edit_authority.pinned(old['accepted_result']['permit'], authority)
            store.readable_event(c, actor, old['event'])
            return {'receipt': old['accepted_result'], 'admission': None, 'replayed': True}
        admission = None
        if command['action'] == 'preflight':
            wanted = command['files']
            paths = [claims.canonical_path(f['relativePath']) for f in wanted]
            need(len(set(paths)) == len(paths), 'file_set_mismatch', 'Unique ordinary files required')
            need(all((f['beforeSha256'] != 'absent' or f['beforeSize'] == 0)
                     and (f['afterSha256'] != 'absent' or f['afterSize'] == 0) for f in wanted),
                 'invalid_request', 'Absent files have zero bytes')
            observed(command['filesObserved'], wanted, 'before')
            need(not c.execute('SELECT 1 FROM hb_edit_permit WHERE id=%s', (command['permitId'],)).fetchone(),
                 'permit_exists', 'Read the original permit; never restart it')
            block_collaboration(c, namespace, paths)
            for path in paths:
                need(not claims.lock_conflict(c, namespace, path), 'scope_busy', 'Unsettled mutation owns physical path')
            proof = {k: payload[k] for k in ('executorId', 'descriptorDigest', 'serviceBindingDigest', 'target')}
            proof.update(files=wanted, patchSha256=command['patchSha256'], sourceRevision=command['sourceRevision'],
                         contextWorkspaceId=str(binding['workspace_id']), hostId=str(binding['physical_host']),
                         workspaceId=str(binding['physical_workspace_id']), workAuthority=authority)
            permit = c.execute('''INSERT INTO hb_edit_permit(id,authority,scope_kind,delivery,room,
                state,expires_at,source,bridge,bridge_revision,proof,consent_digest)
                VALUES(%s,%s,'delivery',%s,%s,'reserved',%s,%s,%s,%s,%s,%s) RETURNING *''',
                (command['permitId'], actor['authority'], source['deliveryId'], request['room'],
                 now(c)+timedelta(seconds=command['leaseSeconds']), Jsonb(source), relay['id'],
                 payload['bridgeRevision'], Jsonb(proof), digest([]))).fetchone()
            for path in paths:
                c.execute('INSERT INTO hb_edit_file_lock VALUES(%s,%s,%s,%s,%s)', (*namespace, path, permit['id']))
        else:
            permit = c.execute('SELECT * FROM hb_edit_permit WHERE id=%s AND authority=%s FOR UPDATE',
                               (command['permitId'], actor['authority'])).fetchone()
            need(permit and permit['scope_kind'] == 'delivery' and permit['revision'] == command['permitRevision'],
                 'stale_revision', 'Exact ordinary permit revision required')
            original_fences(permit, relay, source, binding, payload)
            work_edit_authority.pinned(permit, authority)
            wanted = files(c, permit)
            if command['action'] == 'begin':
                need(permit['state'] == 'reserved' and permit['expires_at'] > now(c),
                     'permit_expired', 'Only live unconsumed ordinary reservation may begin')
                block_collaboration(c, namespace, [f['relativePath'] for f in wanted])
                observed(command['filesObserved'], wanted, 'before')
                permit = c.execute("UPDATE hb_edit_permit SET state='executing',revision=revision+1 WHERE id=%s RETURNING *",
                                   (permit['id'],)).fetchone()
                admission = json_value({'mayApply': True, 'permitId': permit['id'], 'permitRevision': permit['revision'],
                    'expiresAt': permit['expires_at'], 'sourceDigest': digest(source),
                    'patchSha256': permit['proof']['patchSha256'], 'files': wanted, 'oneShotOriginalResponseOnly': True})
            else:
                need(permit['state'] in ('executing', 'held'), 'permit_held', 'Original executing or held mutation required')
                observed_scope(command['filesObserved'], wanted)
                evidence = {'outcome': command['outcome'], 'filesObserved': command['filesObserved'], 'source': source}
                holds = []
                try:
                    need(permit['state'] == 'executing' and permit['expires_at'] > now(c),
                         'permit_expired', 'Expired/held original remains held')
                    block_collaboration(c, namespace, [f['relativePath'] for f in wanted])
                    need(command['outcome'] != 'unknown', 'unknown_effect', 'Unknown or partial mutation remains held')
                    observed(command['filesObserved'], wanted, 'after' if command['outcome'] == 'applied' else 'before')
                except (Conflict, Denied) as error:
                    holds.append(getattr(error, 'code', 'authority_changed'))
                if holds:
                    evidence['holds'] = holds
                permit = c.execute('UPDATE hb_edit_permit SET state=%s,revision=revision+1,settlement=%s WHERE id=%s RETURNING *',
                                   ('held' if holds else 'settled', Jsonb(evidence), permit['id'])).fetchone()
                if not holds:
                    c.execute('DELETE FROM hb_edit_file_lock WHERE permit=%s', (permit['id'],))
        receipt = record(store, c, actor, source, payload, {'schema': CAPABILITY, 'scope': 'delivery', 'permit': permit},
                         PATH, request['room'], receipt_key='deliveryEditReceipt')
        return {'receipt': receipt, 'admission': admission, 'replayed': False}


def read(store, actor_id, identity):
    with store.pool.connection() as c:
        reader = store.actor(c, actor_id)
        permit = c.execute("SELECT * FROM hb_edit_permit WHERE id=%s AND authority=%s AND scope_kind='delivery'",
                           (identity, reader['authority'])).fetchone()
        if not permit:
            raise Denied('Original ordinary mutation unavailable')
        source = permit['source']
        if reader['kind'] == 'service':
            claims.bridge(c, reader, source['hostId'])
        elif str(reader['id']) != source['agentId'] and not reader['administrator']:
            raise Denied('Original ordinary owner required')
        else:
            store.readable_event(c, reader, source['requestId'])
        effective = permit['state']
        if effective != 'settled':
            from .native_effects import validate
            try:
                validate(c, store.actor(c, source['agentId']), source)
                claims.settled_controls(c, {'origin_delivery': source['deliveryId']})
                agent_resources.registered(c, reader['authority'], source, permit['proof']['target'])
                work_edit_authority.read_current(store, c, store.actor(c, source['agentId']), source, permit)
                block_collaboration(c, [str(permit['authority']), permit['proof']['hostId'], permit['proof']['workspaceId']],
                                    [f['relativePath'] for f in files(c, permit)])
                need(effective != 'held' and permit['expires_at'] > now(c), 'permit_held', 'Held original')
            except (Conflict, Denied):
                effective = 'held'
        return json_value({'schema': CAPABILITY, 'scope': 'delivery', 'permit': permit,
                           'effectiveState': effective, 'mayApply': False, 'newExecutionAdmitted': False})


def observe_locked(store, c, relay, payload, permit):
    """Stopped evidence only; no native callback or lock-release authority."""
    path = 'collaboration/mutation-observations'
    source = permit['source']
    profile = claims.bridge(c, relay, source['hostId'], payload['bridgeRevision'])
    need(profile['protocol'] == 'canonical-edit-plans/v2', 'capability_missing', 'Protected observation profile required')
    old = original_command(c, relay, payload, path)
    if old:
        return {'receipt': old['accepted_result'], 'admission': None, 'replayed': True}
    need(permit['state'] != 'settled' and permit['revision'] == payload['permitRevision'],
         'stale_revision', 'Exact unsettled ordinary permit required')
    resource = c.execute('SELECT * FROM hb_executor WHERE id=%s AND delivery=%s AND generation=%s AND claim_id=%s',
                         (payload['executorId'], source['deliveryId'], source['generation'], source['claimId'])).fetchone()
    need(resource and resource['state'] == 'stopped' and resource['observed_at']
         and resource['controller'] == relay['id']
         and (resource['evidence'] or {}).get('reason') in
             ('registered-unit-drained', 'registered-invocation-settled', 'machine-boot-changed')
         and all(payload[k] == permit['proof'][k] for k in ('executorId', 'descriptorDigest', 'serviceBindingDigest'))
         and resource['descriptor_digest'] == payload['descriptorDigest']
         and (resource['evidence'] or {}).get('serviceBindingDigest') == payload['serviceBindingDigest'],
         'executor_unsettled', 'Original independently stopped executor/service binding required')
    from .physical_workspaces import attest
    binding = attest(c, relay, source, {}, payload, cleanup=True, ordinary=True)
    namespace = claims.namespace_lock(c, relay['authority'], binding['physical_host'], binding['physical_workspace_id'])
    need(namespace == [str(relay['authority']), permit['proof']['hostId'], permit['proof']['workspaceId']],
         'scope_mismatch', 'Original physical namespace required')
    observed_scope(payload['filesObserved'], files(c, permit))
    observation = {'outcome': payload['outcome'], 'filesObserved': payload['filesObserved'],
                   'controllerId': str(relay['id']), 'executorId': payload['executorId'],
                   'observedAt': str(now(c)), 'operationId': payload['operationId'], 'note': payload['note']}
    permit = c.execute("UPDATE hb_edit_permit SET state='held',revision=revision+1,observation=%s WHERE id=%s RETURNING *",
                       (Jsonb(observation), permit['id'])).fetchone()
    receipt = record(store, c, relay, source, payload, {'schema': CAPABILITY, 'scope': 'delivery', 'permit': permit,
        'observationDigest': digest(observation), 'hostId': source['hostId'], 'evidenceOnly': True},
        path, permit['room'], native=False, receipt_key='deliveryEditReceipt')
    return {'receipt': receipt, 'admission': None, 'replayed': False}
