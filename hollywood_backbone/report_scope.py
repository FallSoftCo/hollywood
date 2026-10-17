"""Authenticated original-scope snapshot issuance and complete-page evidence.

Server issuance/HTTP coverage is evidence, never model receipt/comprehension.
The original execution is never resumed and active participation never changes.
"""
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value, uid
from .report_scope_models import ReportScopeRead, ReportScopeBriefing

PARTICIPATION = ('authorityId', 'agentId', 'hostId', 'deliveryId', 'generation', 'claimId',
                 'requestId', 'contextId', 'roleRevision', 'nativeThread', 'nativeTurn', 'nativeGoalId')


class ScopeError(Conflict):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(value, code, message):
    if not value:
        raise ScopeError(code, message)


def decisions(store, c, actor, work, goal):
    """All current revisions, including retracted/conflicted, in explicit scopes."""
    rooms = sorted({str(work['room']), str(goal['room'])})
    for room in rooms:
        store.room(c, actor, room)
        c.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))', ('decision-scope:' + room,))
    rows = c.execute('''SELECT d.id,d.room AS "roomId",d.goal AS "goalId",d.key,d.revision,
        v.state,v.body,v.issuer AS "issuerId",p.kind AS "issuerKind",v.source AS "sourceEventId",
        v.event AS "eventId",e.seq AS sequence FROM hb_decision d
        JOIN hb_decision_revision v ON v.decision=d.id AND v.revision=d.revision
        JOIN hb_event e ON e.id=v.event JOIN hb_principal p ON p.id=v.issuer
        WHERE d.authority=%s AND d.room=ANY(%s::uuid[]) AND (d.goal IS NULL OR d.goal=%s)
        ORDER BY d.room,d.id LIMIT 4097''', (actor['authority'], rooms, goal['id'])).fetchall()
    require(len(rows) <= 4096, 'scope_too_large', 'Explicit scope exceeds bounded snapshot capacity; no proof issued')
    return json_value(rows), [{'roomId': r, 'goalId': str(goal['id'])} for r in rooms]


def scope(c, actor, work, goal, prior, attempt, binding):
    from .cooperation import row as policy_row
    issuer = c.execute('SELECT e.author,p.kind FROM hb_event e JOIN hb_principal p ON p.id=e.author WHERE e.id=%s',
                       (prior['event'],)).fetchone()
    policy = policy_row(c, goal['id'])
    return json_value({'authorityId': actor['authority'], 'agentId': actor['id'], 'workId': work['id'],
        'workRevision': work['revision'], 'goalId': goal['id'], 'goalRoomId': goal['room'],
        'goalSourceEventId': goal['source'], 'goalDirectionSha256': digest({k: goal[k] for k in ('title','objective','acceptance')}),
        'roomId': work['room'], 'requestId': prior['event'], 'originalIssuerId': issuer['author'],
        'originalIssuerKind': issuer['kind'], 'deliveryId': prior['id'], 'generation': prior['generation'],
        'claimId': prior['claim_id'], 'hostId': prior['host'], 'workspaceId': binding['workspace_id'],
        'contextId': binding['context_id'], 'nativeThread': prior['native_thread'], 'nativeTurn': prior['native_turn'],
        'originalResultEventId': prior['result_event'], 'attemptNumber': attempt['number'],
        'specificationSha256': digest(work['specification']), 'acceptanceSha256': digest(work['acceptance']),
        'cooperationRevision': policy['revision'] if policy else None})


def same_reader(saved, current):
    return all(saved[k] == current[k] for k in PARTICIPATION)


def proof(row):
    return {'schema': 'completed-report-scope-proof/v1', 'readId': str(row['id']), 'revision': row['revision'],
        'snapshotSha256': row['snapshot_sha256'], 'scopeSha256': row['scope_sha256'],
        'decisionsSha256': row['decisions_sha256'], 'complete': True}


def fresh(c, row, source, current_scope, current_decisions):
    require(same_reader(row['reader'], source), 'foreign_scope_reader', 'Read belongs to another current participation')
    require(row['expires_at'] > c.execute('SELECT clock_timestamp() AS t').fetchone()['t'],
            'scope_expired', 'Scoped read expired; explicitly read the original scope again')
    require(row['scope'] == current_scope, 'original_scope_changed', 'Original work/request/attempt/context scope changed')
    require(row['decisions_sha256'] == digest(current_decisions), 'scope_decisions_changed',
            'Relevant decisions changed; explicitly read the complete original scope again')


def read(store, actor_id, identity, payload, *, wire=None):
    from .native_effects import current_participation
    from .report_reconciliation import fences
    from .work import work_row
    payload = ReportScopeRead.model_validate(payload).model_dump(mode='json', by_alias=True)
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        source = current_participation(c, actor, actions={'report_scope_read'})
        if source['tool'] != 'hollywood_briefing':
            raise Denied('Actual current explicit briefing invocation required')
        operation = payload['operationId']
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (str(actor['id']) + ':' + operation,))
        key = digest({'reportScopeRead': identity, 'payload': payload, 'source': source})
        old = c.execute('SELECT * FROM hb_command WHERE principal=%s AND operation=%s', (actor['id'], operation)).fetchone()
        require(not old or old['digest'] == key, 'operation_changed', 'Original scoped read operation changed')
        work = work_row(store, c, actor, identity)
        goal = c.execute('SELECT * FROM hb_goal WHERE id=%s AND authority=%s FOR UPDATE', (work['goal'], actor['authority'])).fetchone()
        work = work_row(store, c, actor, identity)
        prior, attempt, binding = fences(store, c, actor, work, goal,
            {'priorDeliveryId': payload['priorDeliveryId'], 'evidence': []}, source)
        current_scope = scope(c, actor, work, goal, prior, attempt, binding)
        current_decisions, declared_scopes = decisions(store, c, actor, work, goal)
        if old:
            store.readable_event(c, actor, old['event'])
            row = c.execute('SELECT * FROM hb_report_scope_read WHERE id=%s', (old['accepted_result']['readId'],)).fetchone()
            fresh(c, row, source, current_scope, current_decisions)
            return old['accepted_result']
        require(goal['state'] in ('active','paused') and work['state'] == 'assigned'
                and not work['report_event'] and not attempt['report_event'],
                'original_scope_closed', 'Only original completed unreported assigned work may obtain a new scoped read')
        require(work['revision'] == payload['revision'], 'work_changed', 'Current original work revision required')
        if payload['readId'] is None:
            require(payload['cursor'] == 0, 'scope_page_order', 'Start an explicit original-scope read at cursor0')
            head = c.execute('SELECT next_seq FROM hb_authority WHERE id=%s', (actor['authority'],)).fetchone()['next_seq']
            seq = max((d['sequence'] for d in current_decisions), default=0)
            local_seq = max((d['sequence'] for d in current_decisions if d['roomId'] == str(work['room'])), default=0)
            snapshot = {'scope': current_scope, 'reader': source, 'decisionScopes': declared_scopes,
                'asOfSequence': head, 'decisionSequence': seq, 'originalRoomDecisionSequence': local_seq,
                'decisions': current_decisions}
            row = c.execute('''INSERT INTO hb_report_scope_read(id,authority,principal,work,prior_delivery,
                reader,scope,decisions,decision_scopes,as_of_sequence,decision_sequence,original_room_sequence,
                snapshot_sha256,scope_sha256,decisions_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (uid(), actor['authority'], actor['id'], work['id'], prior['id'], Jsonb(source), Jsonb(current_scope),
                 Jsonb(current_decisions), Jsonb(declared_scopes), head, seq, local_seq,
                 digest(snapshot), digest(current_scope), digest(current_decisions))).fetchone()
        else:
            row = c.execute('SELECT * FROM hb_report_scope_read WHERE id=%s AND authority=%s AND principal=%s FOR UPDATE',
                            (payload['readId'], actor['authority'], actor['id'])).fetchone()
            require(row, 'scope_read_missing', 'Server-issued original scope read is unavailable')
            fresh(c, row, source, current_scope, current_decisions)
            require(not row['complete'], 'scope_read_complete', 'Read already complete; recover its original final-page operation')
        require(payload['cursor'] == row['covered_count'], 'scope_page_order', 'Every original decision page must be served in order')
        total = len(row['decisions'])
        end = min(total, row['covered_count'] + payload['pageSize'])
        page = row['decisions'][row['covered_count']:end]
        row = c.execute('UPDATE hb_report_scope_read SET covered_count=%s,complete=%s,revision=revision+1 WHERE id=%s RETURNING *',
                        (end, end == total, row['id'])).fetchone()
        response = json_value({'schema': 'completed-report-briefing/v1', 'readId': row['id'], 'revision': row['revision'],
            'scope': row['scope'], 'reader': row['reader'], 'decisionScopes': row['decision_scopes'],
            'asOfSequence': row['as_of_sequence'], 'decisionSequence': row['decision_sequence'],
            'originalRoomDecisionSequence': row['original_room_sequence'], 'scopeSha256': row['scope_sha256'],
            'decisionsSha256': row['decisions_sha256'], 'snapshotSha256': row['snapshot_sha256'],
            'decisionCount': total, 'coveredCount': end, 'decisions': page, 'complete': row['complete'],
            'nextCursor': None if row['complete'] else end, 'scopeRead': proof(row) if row['complete'] else None,
            'issuedAt': row['issued_at'], 'expiresAt': row['expires_at'],
            'contentAuthority': 'tool-data-with-attributable-issuers',
            'provenance': 'Authenticated scoped pages served by server; model receipt/comprehension not established',
            'newExecutionAdmitted': False, 'currentParticipationUnchanged': True})
        ReportScopeBriefing.model_validate(response)
        event = store.event(c, actor, work['room'], 'progress', 'Original report-scope page served',
            source=source['requestId'], audience='direct', recipient=actor['id'],
            data={'type': 'reportScopeProtocol', 'scopeReadReceipt': response,
                'nativeAction': {**source, 'operationId': operation}})
        c.execute('''INSERT INTO hb_command(authority,principal,operation,digest,event,wire_request,accepted_result)
            VALUES(%s,%s,%s,%s,%s,%s,%s)''', (actor['authority'], actor['id'], operation, key, event,
            Jsonb({**wire, 'nativeSource': source}), Jsonb(response)))
        return response


def verify(store, c, actor, work, goal, prior, attempt, binding, source, supplied):
    require(supplied, 'scope_read_required', 'An explicit complete server-issued original-scope read is required')
    row = c.execute('SELECT * FROM hb_report_scope_read WHERE id=%s AND authority=%s AND principal=%s FOR SHARE',
                    (supplied['readId'], actor['authority'], actor['id'])).fetchone()
    require(row, 'scope_read_missing', 'Scoped read is missing or belongs to another principal/authority')
    current_scope = scope(c, actor, work, goal, prior, attempt, binding)
    current_decisions, _ = decisions(store, c, actor, work, goal)
    fresh(c, row, source, current_scope, current_decisions)
    require(row['complete'] and row['covered_count'] == len(row['decisions']),
            'scope_read_incomplete', 'Partial/truncated scoped pages cannot authorize reconciliation')
    require(proof(row) == supplied, 'scope_proof_changed', 'Exact original server-issued proof required')
    return json_value({'schema': 'completed-report-scope-verification/v1', 'proof': proof(row),
        'scope': row['scope'], 'reader': row['reader'], 'decisionScopes': row['decision_scopes'],
        'decisions': row['decisions'], 'decisionSequence': row['decision_sequence'],
        'originalRoomDecisionSequence': row['original_room_sequence'], 'asOfSequence': row['as_of_sequence'],
        'servedAt': row['issued_at'], 'expiresAt': row['expires_at'],
        'provenance': 'Server-issued authenticated complete HTTP snapshot; no model receipt/comprehension claim'})


def verify_review(store, c, actor, work, goal, audit):
    value = audit.get('scopeVerification')
    require(value, 'scope_read_required',
            'Historical unscoped recovery evidence cannot establish new acceptance; explicit reviewed rework required')
    current, declared_scopes = decisions(store, c, actor, work, goal)
    require(declared_scopes == value['decisionScopes'] and digest(current) == value['proof']['decisionsSha256'],
            'scope_decisions_changed', 'Relevant decisions changed after this reconciled submission; explicit rework required')
