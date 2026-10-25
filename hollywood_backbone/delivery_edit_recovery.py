"""Reviewed cleanup of original held file edits, never a new mutation admission."""
from psycopg.types.json import Jsonb

from . import agent_resources, collaboration as claims, owned_work_claims
from .edit_plan_scope import need, now, observed, record
from .store import digest, json_value

PATH = 'collaboration/delivery-edit-reconciliations'


def reconcile(store, c, actor, identity, payload, *, required_scope="delivery"):
    need(payload['note'].strip(), 'invalid_request', 'Specific review note required')
    # administrative.mutate authenticates/fences the current administrator and
    # journals the original operation. The stopped controller supplies facts;
    # the administrator explicitly reviews their exact immutable digest.
    permit = c.execute(
        "SELECT * FROM hb_edit_permit WHERE id=%s AND authority=%s FOR UPDATE",
        (identity, actor['authority'])).fetchone()
    need(permit and permit['scope_kind'] in ('delivery', 'plan')
         and (required_scope is None or permit['scope_kind'] == required_scope),
         'scope_mismatch', 'Exact original mutation scope required')
    plan = None
    if permit['scope_kind'] == 'plan':
        from .edit_plan_scope import plan_row, claims_snapshot
        plan = plan_row(c, actor['authority'], str(permit['plan']))
        version = c.execute('SELECT * FROM hb_edit_plan_revision WHERE plan=%s AND revision=%s',
                            (permit['plan'], permit['plan_revision'])).fetchone()
        need(version and version['manifest_digest'] == permit['proof']['manifestSha256']
             and plan['revision'] == permit['plan_revision'],
             'scope_changed', 'Original plan/file manifest required')
        wanted = version['manifest']['files']
        claims_snapshot(c, plan, version['manifest'], allow_expired=True)
        room = plan['room']
    else:
        wanted = permit['proof']['files']
        room = permit['room']
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
    if plan is None:
        root, _ = agent_resources.registered(c, actor['authority'], source, proof['target'], cleanup=True)
    else:
        from .physical_workspaces import registered
        root, _ = registered(c, actor['authority'], source, str(plan['integrator_work']),
                             proof['target'], cleanup=True)
    namespace = claims.namespace_lock(c, actor['authority'], root['host'], root['id'])
    need(namespace == [str(actor['authority']), proof['hostId'], proof['workspaceId']],
         'scope_mismatch', 'Original physical lock namespace required')
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
        **({'ownershipTransitions': owned_work_claims.advance(c, permit, payload['outcome'])}
           if plan is None else {}),
        'classification': 'observed-original-after-content' if payload['outcome'] == 'applied'
                          else 'observed-original-before-content',
        'externalEffectsUndone': False,
    }
    if plan is None:
        permit = c.execute(
            "UPDATE hb_edit_permit SET state='settled',revision=revision+1,settlement=%s "
            'WHERE id=%s RETURNING *', (Jsonb(settlement), permit['id'])).fetchone()
        c.execute('DELETE FROM hb_edit_file_lock WHERE permit=%s', (permit['id'],))
    else:
        from .edit_plans import finish
        permit = finish(c, plan, version['manifest'], permit, payload['outcome'], settlement)
    generic = required_scope is None
    receipt = record(store, c, actor, source, payload,
                     {'schema': 'hollywood-mutation/v1' if generic else 'canonical-delivery-edits/v1',
                      'scope': permit['scope_kind'], 'permit': permit, 'fileManifest': wanted,
                      'reviewedBy': str(actor['id'])},
                     ('collaboration/mutation-reconciliations' if generic else PATH) + '/' + identity,
                     room, native=False, receipt_key='mutationReceipt' if generic else 'deliveryEditReceipt',
                     evidence_only=False)
    return json_value({'receipt': receipt, 'admission': None, 'replayed': False})


def read(store, actor_id, identity):
    """One original mutation, independently of its ordinary/planned scope."""
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        permit = c.execute("SELECT * FROM hb_edit_permit WHERE id=%s AND authority=%s",
                           (identity, actor["authority"])).fetchone()
        if not permit:
            from .store import Denied
            raise Denied("Original mutation unavailable")
        if permit["scope_kind"] == "delivery":
            from .delivery_edits import read as ordinary_read
            result = ordinary_read(store, actor_id, identity)
            return {**result, "schema": "hollywood-mutation/v1",
                    "fileManifest": result["permit"]["proof"]["files"]}
        from .edit_plans import authorize_read, active_fences
        from .edit_plan_scope import plan_row
        plan = plan_row(c, actor["authority"], str(permit["plan"]))
        authorize_read(store, c, actor, plan)
        version = c.execute("SELECT * FROM hb_edit_plan_revision WHERE plan=%s AND revision=%s",
                            (permit["plan"], permit["plan_revision"])).fetchone()
        need(version and version["manifest_digest"] == permit["proof"]["manifestSha256"],
             "scope_changed", "Original mutation manifest required")
        effective = permit["state"]
        if effective != "settled":
            from .store import Conflict, Denied
            try:
                need(effective != "held" and permit["expires_at"] > now(c),
                     "permit_held", "Held original")
                active_fences(store, c, plan, version, permit)
            except (Conflict, Denied):
                effective = "held"
        return json_value({"schema": "hollywood-mutation/v1", "scope": "plan",
            "planId": str(permit["plan"]), "permit": permit,
            "fileManifest": version["manifest"]["files"], "effectiveState": effective,
            "mayApply": False, "newExecutionAdmitted": False})
