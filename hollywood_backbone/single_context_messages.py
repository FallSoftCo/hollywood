"""Durable typed replies to explicitly requested peers, not broadcast wakeups.

Reuse canonical events and the existing host-admitted delivery queue. Membership,
ordinary messages and activity never enqueue work here. Purpose runtimes retain
their own native inbox. No model is called by this projection.
"""
from uuid import UUID,uuid5
from .store import json_value
CAPABILITY='single-context-peer-completions/v1'


def origin(c, actor, delivery, event):
    if (event['recipient'] != actor['id'] or event['data'].get('type')!='peerCompletion'
            or event['data'].get('authorKind')!='agent' or event['kind'] not in ('result','message')):
        return None
    row=c.execute('''SELECT p.id AS peer_request,p.source AS origin_request,
        d.id AS peer_delivery,d.result_event,o.id AS origin_delivery,
        x.context_id,x.native_thread
        FROM hb_delivery d JOIN hb_event p ON p.id=d.event
        JOIN hb_delivery o ON o.event=p.source AND o.agent=%s
        JOIN hb_agent_context x ON x.principal=o.agent AND x.native_thread=o.native_thread
        WHERE d.id::text=%s AND p.id::text=%s AND d.authority=%s
          AND p.kind='request' AND p.author=%s AND p.recipient=d.agent
          AND p.data->>'expectsReply'='true' AND d.agent=%s
          AND d.state IN ('completed','failed') AND NOT d.cancellation_requested
          AND d.result_event=%s AND o.native_turn IS NOT NULL
          AND NOT o.cancellation_requested AND o.state<>'cancelled'
          AND NOT EXISTS(SELECT 1 FROM hb_context_system s WHERE s.agent=o.agent)
        ''',(actor['id'],event['data'].get('replyToDelivery'),event['data'].get('replyToRequest'),
             actor['authority'],actor['id'],event['author'],event['source'])).fetchone()
    if not row:return None
    return json_value({'schema':'lax-peer-completion/v1',
        'replyToRequest':row['peer_request'],'replyToDelivery':row['peer_delivery'],
        'resultEventId':row['result_event'],'originRequestId':row['origin_request'],
        'originDeliveryId':row['origin_delivery'],'contextId':row['context_id'],
        'nativeThread':row['native_thread']})


def enqueue(store,c,actor,host,capabilities):
    if CAPABILITY not in capabilities or actor['kind']!='agent':return 0
    if c.execute('SELECT 1 FROM hb_context_system WHERE agent=%s',(actor['id'],)).fetchone():return 0
    placed=c.execute('SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s',(actor['id'],host)).fetchone()
    if not placed:return 0
    # Indexed by authority/recipient/seq. Only actual requested-peer completions
    # can be projected; unique(event,agent) provides restart-safe deduplication.
    events=c.execute('''SELECT e.* FROM hb_event e
        JOIN hb_delivery answered ON answered.result_event=e.source AND answered.agent=e.author
        JOIN hb_event asked ON asked.id=answered.event AND asked.author=e.recipient
        JOIN hb_delivery parent ON parent.event=asked.source AND parent.agent=e.recipient
        JOIN hb_agent_context context ON context.principal=parent.agent
             AND context.native_thread=parent.native_thread
        WHERE e.authority=%s AND e.recipient=%s AND e.data->>'type'='peerCompletion'
          AND e.data->>'authorKind'='agent' AND e.kind IN ('result','message')
          AND asked.kind='request' AND asked.recipient=answered.agent
          AND asked.data->>'expectsReply'='true'
          AND answered.id::text=e.data->>'replyToDelivery'
          AND asked.id::text=e.data->>'replyToRequest'
          AND answered.state IN ('completed','failed') AND NOT answered.cancellation_requested
          AND parent.native_turn IS NOT NULL AND NOT parent.cancellation_requested
          AND parent.state<>'cancelled'
          AND (%s OR EXISTS(SELECT 1 FROM hb_membership m WHERE m.room=e.room
              AND m.principal=e.recipient AND m.can_read AND m.can_post))
          AND NOT EXISTS(SELECT 1 FROM hb_delivery d WHERE d.event=e.id AND d.agent=e.recipient)
        ORDER BY e.seq LIMIT 16''',(actor['authority'],actor['id'],actor['administrator'])).fetchall()
    count=0
    for event in events:
        if not origin(c,actor,None,event):continue
        identity=uuid5(UUID(str(event['id'])),'single-context-peer-completion')
        accepted=c.execute('''INSERT INTO hb_delivery(id,authority,event,agent)
            VALUES(%s,%s,%s,%s) ON CONFLICT(event,agent) DO NOTHING RETURNING id''',
            (identity,actor['authority'],event['id'],actor['id'])).fetchone()
        count+=int(accepted is not None)
    return count
