"""Business allocation is independent of file collaboration, never file access.

Derive the original admitted Work from canonical request/attempt records. The
protected delivery source and per-agent physical grant remain separate fences.
"""
from . import collaboration as claims
from .cooperation import row as cooperation
from .edit_plan_scope import need
from .memory import current_sequence
from .store import digest, json_value

CAPABILITY = 'canonical-work-delivery-edits/v1'


def current(store, c, actor, source, request, binding=None):
    attempt = c.execute('SELECT * FROM hb_work_attempt WHERE delivery=%s AND authority=%s',
                        (source['deliveryId'], actor['authority'])).fetchone()
    linked = c.execute('SELECT id FROM hb_work WHERE delivery=%s AND authority=%s',
                       (source['deliveryId'], actor['authority'])).fetchone()
    data = request['data']
    if data.get('workId') is None:
        need(not attempt and not linked, 'work_changed', 'Canonical Work cannot be hidden by an ordinary request')
        return None
    work, goal = claims.work_scope(store, c, actor, data['workId'])
    policy = cooperation(c, goal['id'])
    need(request['kind'] == 'request' and str(request['recipient']) == str(actor['id'])
         and str(work['delivery']) == source['deliveryId'] and str(work['request_event']) == source['requestId']
         and str(work['room']) == str(request['room']) and str(goal['id']) == data.get('goalId')
         and data.get('attemptId') == source['deliveryId'] and data.get('workCompletionPolicy') == 'report-required/v1',
         'work_changed', 'Current original assignment/request/delivery required')
    need(type(data.get('workRevision')) is int and work['revision'] == data['workRevision'],
         'work_changed', 'Captured assignment revision changed')
    need(attempt and linked and linked['id'] == work['id'] and attempt['work'] == work['id']
         and attempt['owner'] == actor['id'] and str(attempt['request_event']) == source['requestId']
         and attempt['origin'] == 'native-admission' and type(data.get('attemptNumber')) is int
         and attempt['number'] == data['attemptNumber']
         and (attempt['specification'], attempt['acceptance']) == (work['specification'], work['acceptance']),
         'work_changed', 'Exact admitted Work attempt and original definition required')
    need(data.get('cooperationRevision') == (policy['revision'] if policy else None),
         'work_changed', 'Original cooperation eligibility changed')
    # Reuse dependency acceptance rather than treating a Goal as effect authority.
    need(not c.execute('''SELECT 1 FROM hb_work_dependency d JOIN hb_work p ON p.id=d.prerequisite
        WHERE d.work=%s AND p.state<>'accepted' LIMIT 1''', (work['id'],)).fetchone(),
        'work_changed', 'Current prerequisite acceptance required')
    if binding is None:
        from .agent_context import execution
        binding = execution(c, source['deliveryId'], source['generation'])
    need(binding and str(binding['principal']) == source['agentId']
         and binding['context_id'] == source['contextId'] and binding['native_thread'] == source['nativeThread']
         and binding['role_revision'] == source['roleRevision'] and source['nativeGoalId'] is None,
         'work_changed', 'Original persistent native execution/role/context required')
    context = c.execute('''SELECT 1 FROM hb_agent_context WHERE principal=%s AND authority=%s
        AND context_id=%s AND native_thread=%s AND workspace_id=%s AND host=%s''',
        (actor['id'], actor['authority'], source['contextId'], source['nativeThread'],
         binding['workspace_id'], source['hostId'])).fetchone()
    need(context, 'work_changed', 'Current private context must match original Work execution')
    placement = c.execute('SELECT revision FROM hb_placement WHERE agent=%s AND host=%s AND enabled',
                          (actor['id'], source['hostId'])).fetchone()
    need(placement, 'work_changed', 'Current enabled placement required')
    membership = c.execute('SELECT revision FROM hb_membership WHERE room=%s AND principal=%s',
                           (work['room'], actor['id'])).fetchone()
    definition = {key: attempt[key] for key in ('delivery', 'authority', 'work', 'number', 'owner',
                  'request_event', 'specification', 'acceptance', 'origin')}
    return json_value({'schema': CAPABILITY, 'workId': work['id'], 'workRevision': work['revision'],
        'goalId': goal['id'], 'goalRevision': goal['revision'],
        'goalControlRevision': goal['control_revision'],
        'decisionSequence': current_sequence(c, str(work['room']), str(goal['id'])),
        'cooperationRevision': policy['revision'] if policy else None,
        'placementRevision': placement['revision'],
        'roomGrantRevision': membership['revision'] if membership else None,
        'attemptId': attempt['delivery'], 'attemptNumber': attempt['number'], 'ownerId': actor['id'],
        'requestId': request['id'], 'roomId': work['room'], 'attemptDigest': digest(json_value(definition)),
        'executionDigest': binding['digest']})


def pinned(permit, authority):
    # Older ordinary permits have no key. They remain ordinary; they cannot
    # acquire a Work scope through recovery or a later request mutation.
    original = permit['proof'].get('workAuthority')
    # Older permits retain their original stricter revision fence. New permits
    # fence the independent Goal control epoch, allowing sibling Work progress.
    if isinstance(original, dict) and 'goalControlRevision' in original:
        def comparable(value):
            return ({key: item for key, item in value.items() if key != 'goalRevision'}
                    if isinstance(value, dict) else value)
        same = comparable(original) == comparable(authority)
    else:
        current = ({key: item for key, item in authority.items() if key != 'goalControlRevision'}
                   if isinstance(authority, dict) else authority)
        same = original == current
    need(same, 'work_changed',
         'Original Work/goal/attempt authority changed; retain original permit and locks')



def read_current(store, c, actor, source, permit):
    request = store.readable_event(c, actor, source['requestId'])
    pinned(permit, current(store, c, actor, source, request))
