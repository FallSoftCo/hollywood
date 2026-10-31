"""Human callback communication for a native conversation, not an executor job."""
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value

SCHEMA='''
ALTER TABLE hb_attention ALTER COLUMN delivery DROP NOT NULL;
ALTER TABLE hb_attention ALTER COLUMN generation DROP NOT NULL;
ALTER TABLE hb_attention ALTER COLUMN claim_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS hb_attention_conversation_slot
 ON hb_attention(agent,native_thread,native_turn,native_slot) WHERE delivery IS NULL;
'''
QUESTION={'hollywood/question','item/tool/requestUserInput'}


def scope(row):
    return json_value({'conversationVersion':1,'authorityId':row['authority'],'attentionId':row['id'],
        'agentId':row['agent'],'roomId':row['room'],'sourceEventId':row['event'],
        'nativeThread':row['native_thread'],'nativeTurn':row['native_turn'],'requestDigest':row['digest']})


def receipt(store,c,actor,row):
    store.readable_event(c,actor,row['event'])
    if actor['id'] not in (row['human'],row['agent']):raise Denied('Original attention participant required')
    store.room(c,actor,row['room'])
    return {'attention':json_value(row),'scope':scope(row),'deliveryState':None,
        'nativeConsumptionProved':False,'canRespond':actor['kind']=='human' and actor['id']==row['human'] and row['state']=='pending',
        'responseMode':'native-callback','continuation':None}


def create(store,actor_id,identity,payload):
    from .attention import human_owner
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        if actor['kind']!='agent':raise Denied('Agent callback publisher required')
        source=store.readable_event(c,actor,payload['sourceEventId'])
        if payload.get('roomId') is not None and str(source['room'])!=payload['roomId']:raise Denied('Callback room differs from original source')
        if str(actor['id']) not in source['data'].get('conversationRecipients',[]):
            raise Denied('Original message recipient required')
        store.room(c,actor,source['room'],'can_post')
        human=human_owner(c,source);h=store.actor(c,human);store.room(c,h,source['room'])
        if h['kind']!='human':raise Denied('Attributable human required')
        fingerprint=digest({k:payload[k] for k in ('method','request','summary')})
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',('conversation-attention:'+identity,))
        old=c.execute('SELECT * FROM hb_attention WHERE id=%s FOR UPDATE',(identity,)).fetchone()
        if old:
            if (old['delivery'] is not None or old['agent']!=actor['id'] or str(old['event'])!=payload['sourceEventId']
                or old['native_thread']!=payload['nativeThread'] or old['native_turn']!=payload['nativeTurn']
                or old['native_slot']!=payload['nativeSlot'] or old['digest']!=fingerprint):raise Conflict('Original callback identity changed')
            return receipt(store,c,actor,old)
        if payload['method'] in QUESTION and payload['request'].get('isBlocking') is not True:
            raise ValueError('Conversational attention currently requires original blocking callback')
        store.event(c,actor,source['room'],'question' if payload['method'] in QUESTION else 'approval',payload['summary'],
            recipient=human,audience='direct',source=source['id'],data={'attentionId':identity,'conversationVersion':1,'requiresResponse':True,'state':'pending'})
        c.execute('''INSERT INTO hb_attention(id,authority,agent,human,room,event,native_thread,native_turn,native_slot,method,request,digest)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (identity,actor['authority'],actor['id'],human,source['room'],source['id'],payload['nativeThread'],payload['nativeTurn'],payload['nativeSlot'],payload['method'],Jsonb(payload['request']),fingerprint))
        row=c.execute('SELECT * FROM hb_attention WHERE id=%s',(identity,)).fetchone()
        return receipt(store,c,actor,row)


def mutate(store,c,actor,identity,operation,payload,*,acknowledge=False):
    row=c.execute('SELECT * FROM hb_attention WHERE id=%s AND authority=%s FOR UPDATE',(identity,actor['authority'])).fetchone()
    if not row or row['delivery'] is not None:raise Denied('Conversation callback unavailable')
    receipt(store,c,actor,row)
    if payload['scope']!=scope(row):raise Conflict('Original callback scope changed')
    if acknowledge:
        if actor['kind']!='agent' or actor['id']!=row['agent']:raise Denied('Original native agent required')
    elif actor['kind']!='human' or actor['id']!=row['human']:raise Denied('Original human decision owner required')
    fingerprint=digest(payload)
    old=c.execute('SELECT * FROM hb_attention_command WHERE attention=%s AND principal=%s AND operation=%s',(identity,actor['id'],operation)).fetchone()
    if old:
        if old['digest']!=fingerprint:raise Conflict('Original attention operation changed')
        return old['result']
    if acknowledge:
        state=payload['state'];allowed={'pending':{'expired','unknown'},'response_submitted':{'native_submitted','expired','unknown'},'native_submitted':{'closed','unknown'},'unknown':{'expired','closed'}}
        if state not in allowed.get(row['state'],set()):raise Conflict('Invalid callback transition')
        c.execute('UPDATE hb_attention SET state=%s,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s',(state,identity))
    else:
        if row['state']!='pending':raise Conflict('Original callback no longer pending')
        response=payload['response']
        if row['method'] in QUESTION:
            if set(response.get('answers',{}))!={q['id'] for q in row['request']['questions']}:raise ValueError('Answer every original question')
        elif response.get('decision') not in ('accept','decline'):raise ValueError('Approval requires explicit accept or decline')
        eid=store.event(c,actor,row['room'],'control','Human response accepted; native submission pending',recipient=row['agent'],audience='direct',source=row['event'],data={'attentionId':identity,'conversationVersion':1,'state':'response_submitted'})
        c.execute("UPDATE hb_attention SET response=%s,response_author=%s,response_event=%s,state='response_submitted',revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",(Jsonb(response),actor['id'],eid,identity))
    current=c.execute('SELECT * FROM hb_attention WHERE id=%s',(identity,)).fetchone();result=receipt(store,c,actor,current)
    c.execute('INSERT INTO hb_attention_command VALUES(%s,%s,%s,%s,%s)',(identity,actor['id'],operation,fingerprint,Jsonb(result)))
    return result
