"""Narrow original-source disposition for retained, non-current participants.

Read retention is not new-task authority. Only a pre-handoff send-time input
actually addressed to this old actor can justify its public/private outcome.
"""

def retired_generation(c,actor,room):
    row=c.execute("SELECT * FROM hb_room_agent_generation WHERE room=%s AND authority=%s AND phase='active'",
                   (room,actor['authority'])).fetchone()
    return row if row and actor['id'] in row['retired'] else None


def original_input(c,actor,room,event,generation=None):
    generation=generation or retired_generation(c,actor,room)
    return bool(generation and generation['handoff_seq'] is not None and
        str(event['room'])==str(room) and event['seq']<=generation['handoff_seq'] and
        str(actor['id']) in event['data'].get('conversationRecipients',[]))


def publication(store,c,actor,room,payload):
    if (payload.get('kind')!='message' or payload.get('responsePhase') not in ('update','answer','blocked')
            or payload.get('expectsReply') or not payload.get('sourceEventId')):
        return False
    generation=retired_generation(c,actor,room)
    if not generation:return False
    parent=store.readable_event(c,actor,payload['sourceEventId'])
    if not original_input(c,actor,room,parent,generation) or payload['audience']!=parent['audience']:
        return False
    if parent['audience']=='room' and payload.get('recipientId') not in (None,str(parent['author'])):
        return False
    # Private counterpart equality and recipient access are ALSO checked by
    # normal Store.post. This exception never widens the source audience.
    return True


def observation(store,c,actor,room,payload):
    generation=retired_generation(c,actor,room)
    if not generation:return False
    source=payload.get('sourceEventId')
    known=bool(c.execute('SELECT 1 FROM hb_conversation_state WHERE room=%s AND agent=%s AND native_thread=%s',
        (room,actor['id'],payload['nativeThread'])).fetchone())
    if source:
        return known and original_input(c,actor,room,store.readable_event(c,actor,source),generation)
    # A terminal/status update for an ALREADY observed old native thread can
    # settle old work. It cannot register a new child or claim a new input.
    return payload['status']['type']!='active' and known
