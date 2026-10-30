"""Room communication fan-out, independent of execution/admission tables.

Each member reads the same immutable room events using its own durable runtime
cursor. A read is not an execution claim, acknowledgement or model activation.
"""
from .store import Conflict, Denied, json_value


def feed(store, actor_id, after, limit):
    if (after is not None and (type(after) is not int or not 0 <= after <= 9007199254740991)) or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid conversation cursor or limit')
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        if actor['kind']!='agent':
            raise Denied('Agent conversation inbox required')
        head=c.execute('SELECT next_seq FROM hb_authority WHERE id=%s',
                       (actor['authority'],)).fetchone()['next_seq']
        if after is not None and after>head:
            raise Conflict('Conversation cursor exceeds retained authority history')
        result={'authorityId':str(actor['authority']),'principalId':str(actor['id']),
                'view':'conversation','events':[],'nextCursor':head,'head':head}
        # Immutable send-time recipients prevent late joiners from treating
        # historical conversation as new input. First read includes outstanding
        # messages, including those accepted before the runtime first connected.
        after = 0 if after is None else after
        rows=c.execute("""SELECT e.* FROM hb_event e
            WHERE e.authority=%s AND e.seq>%s AND e.seq<=%s
            AND e.author<>%s AND e.kind IN ('message','request','result','question')
            AND e.data->>'authorKind' IN ('human','agent')
            AND e.data->'conversationRecipients' ? %s
            AND EXISTS(SELECT 1 FROM hb_membership m WHERE m.room=e.room
                       AND m.principal=%s AND m.can_read)
            AND (e.audience='room' OR e.recipient=%s)
            ORDER BY e.seq LIMIT %s""",
            (actor['authority'],after,head,actor['id'],str(actor['id']),actor['id'],actor['id'],limit)).fetchall()
        result['events']=json_value(rows)
        result['nextCursor']=rows[-1]['seq'] if len(rows)==limit else head
        return result


def recipients(c, actor, room, kind, audience, recipient):
    """Snapshot the communication audience in the publishing transaction."""
    if actor['kind'] not in ('human','agent') or kind not in ('message','request','result','question'):
        return None
    rows=c.execute("""SELECT p.id FROM hb_membership m JOIN hb_principal p ON p.id=m.principal
        WHERE m.room=%s AND m.authority=%s AND m.can_read AND p.active
        AND p.kind='agent' AND p.id<>%s
        AND (%s='room' OR p.id=%s) ORDER BY p.id""",
        (room,actor['authority'],actor['id'],audience,recipient)).fetchall()
    return [str(row['id']) for row in rows]
