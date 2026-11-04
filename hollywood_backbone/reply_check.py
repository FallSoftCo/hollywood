"""Conditional communication append; no speaker assignment or execution ownership."""
from .store import Denied, json_value


def check(c, actor, room, payload, source):
    condition = payload.get('replyCheck')
    if condition is None:
        return None
    if (actor['kind'] != 'agent' or payload['kind'] != 'message'
            or payload['audience'] != 'room' or payload.get('recipientId')
            or payload.get('responsePhase') not in ('message', 'answer', 'update')
            or not source or str(source['room']) != room
            or source['audience'] != 'room' or source.get('recipient')
            or source['data'].get('authorKind') != 'human'):
        raise Denied('Reply check requires the original public human source')
    observed = condition['observedThrough']
    # Same authority row as Store.event: a simultaneous append cannot commit
    # between the peer check and this transaction's eventual message append.
    ceiling = c.execute('SELECT next_seq FROM hb_authority WHERE id=%s FOR UPDATE',
                        (actor['authority'],)).fetchone()['next_seq']
    if type(observed) is not int or not source['seq'] <= observed <= ceiling:
        raise ValueError('Observed reply cursor outside the original source snapshot')
    rows = c.execute("""SELECT id,seq,author,body,data->>'responsePhase' AS phase
        FROM hb_event WHERE room=%s AND data->>'humanInputEventId'=%s
        AND author<>%s AND seq>%s AND seq<=%s AND audience='room'
        AND kind='message' AND data->>'authorKind'='agent'
        ORDER BY seq DESC LIMIT 7""",
        (room,str(source['id']),actor['id'],observed,ceiling)).fetchall()
    if not rows:
        return None
    # A conditional rejection is a successful check, NOT an accepted message.
    # It creates no event/command/execution and traverses existing federation
    # unchanged. Bounded previews retain IDs; full originals stay in history.
    peers = [{'id':r['id'],'seq':r['seq'],'author':r['author'],
              'responsePhase':r['phase'],'bodyPreview':r['body'].encode()[:700].decode('utf-8','ignore'),
              'truncated':len(r['body'].encode())>700} for r in reversed(rows[:6])]
    return json_value({'accepted':False,'mayApply':False,
        'code':'REPLY_CONTEXT_CHANGED','roomId':room,'sourceEventId':source['id'],
        'observedThrough':ceiling,'peerReplies':peers,'more':len(rows)>6})
