"""Reviewed cleanup of held ordinary edits, never a new mutation admission."""
from psycopg.types.json import Jsonb

from . import agent_resources, collaboration as claims
from .edit_plan_scope import need, now, observed, record
from .store import digest, json_value

PATH = 'collaboration/delivery-edit-reconciliations'


def reconcile(store, c, actor, identity, payload):
    need(payload['note'].strip(), 'invalid_request', 'Specific review note required')
    # administrative.mutate authenticates/fences the current administrator and
    # journals the original operation. The stopped controller supplies facts;
    # the administrator explicitly reviews their exact immutable digest.
    permit = c.execute(
        "SELECT * FROM hb_edit_permit WHERE id=%s AND authority=%s FOR UPDATE",
        (identity, actor['authority'])).fetchone()
    need(permit and permit['scope_kind'] == 'delivery', 'scope_mismatch',
         'Exact original ordinary permit required')
    need(permit['state'] == 'held' and permit['revision'] == payload['permitRevision'],
         'stale_revision', 'Exact held permit revision required')
    evidence = permit['observation']
    need(evidence and digest(evidence) == payload['observationDigest'],
         'observation_changed', 'Exact protected stopped-executor observation required')
    need(evidence['outcome'] == payload['outcome'], 'unknown_effect',
         'Unknown/mixed observations cannot release ownership')
    # Freshness refuses old facts. Time never releases a permit by itself.
    fresh = c.execute("SELECT %s::timestamptz >= clock_timestamp()-interval '5 minutes' AS ok",
                      (evidence['observedAt'],)).fetchone()['ok']
    need(fresh, 'observation_stale', 'Fresh protected physical observation required')
    source, proof = permit['source'], permit['proof']
    resource = c.execute(
        'SELECT * FROM hb_executor WHERE id=%s AND authority=%s AND delivery=%s '
        'AND generation=%s AND claim_id=%s',
        (evidence['executorId'], actor['authority'], source['deliveryId'],
         source['generation'], source['claimId'])).fetchone()
    need(resource and resource['state'] == 'stopped' and resource['observed_at']
         and str(resource['controller']) == evidence['controllerId']
         and str(resource['id']) == proof['executorId']
         and resource['descriptor_digest'] == proof['descriptorDigest']
         and (resource['evidence'] or {}).get('serviceBindingDigest') == proof['serviceBindingDigest']
         and (resource['evidence'] or {}).get('reason') in
         ('registered-unit-drained', 'registered-invocation-settled', 'machine-boot-changed'),
         'executor_unsettled', 'Original executor must remain independently stopped')
    root, _ = agent_resources.registered(c, actor['authority'], source, proof['target'], cleanup=True)
    namespace = claims.namespace_lock(c, actor['authority'], root['host'], root['id'])
    need(namespace == [str(actor['authority']), proof['hostId'], proof['workspaceId']],
         'scope_mismatch', 'Original physical lock namespace required')
    wanted = proof['files']
    observed(evidence['filesObserved'], wanted,
             'after' if payload['outcome'] == 'applied' else 'before')
    locked = c.execute('SELECT relative_path FROM hb_edit_file_lock WHERE permit=%s',
                       (permit['id'],)).fetchall()
    need({r['relative_path'] for r in locked} == {f['relativePath'] for f in wanted},
         'scope_changed', 'Exact original all-file ownership required')
    settlement = {
        'outcome': payload['outcome'], 'observationDigest': payload['observationDigest'],
        'reviewerId': str(actor['id']), 'reviewedAt': str(now(c)), 'note': payload['note'],
        'effectDisposition': 'reviewed', 'reconciliationOnly': True,
        'originalSettlement': permit['settlement'],
        'classification': 'observed-original-after-content' if payload['outcome'] == 'applied'
                          else 'observed-original-before-content',
        'externalEffectsUndone': False,
    }
    permit = c.execute(
        "UPDATE hb_edit_permit SET state='settled',revision=revision+1,settlement=%s "
        'WHERE id=%s RETURNING *', (Jsonb(settlement), permit['id'])).fetchone()
    c.execute('DELETE FROM hb_edit_file_lock WHERE permit=%s', (permit['id'],))
    receipt = record(store, c, actor, source, payload,
                     {'schema': 'canonical-delivery-edits/v1', 'scope': 'delivery',
                      'permit': permit, 'reviewedBy': str(actor['id'])},
                     PATH + '/' + identity, permit['room'], native=False,
                     receipt_key='deliveryEditReceipt', evidence_only=False)
    return json_value({'receipt': receipt, 'admission': None, 'replayed': False})
