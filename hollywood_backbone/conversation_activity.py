"""Authenticated native observations; no execution admission or chat fan-out.

The runtime attests local native items. Hollywood verifies author, originating
room and audience, not whether a command really executed on an alleged host.
"""
import json
from typing import Literal
from uuid import UUID
from pydantic import Field
from psycopg.types.json import Jsonb
from .activity_models import Input, ActivityItem
from .store import Conflict, Denied, digest, json_value

class Observation(Input):
    operationId: str = Field(min_length=1,max_length=128)
    sourceEventId: UUID
    nativeThread: str = Field(min_length=1,max_length=128)
    nativeTurn: str = Field(min_length=1,max_length=128)
    itemId: str = Field(min_length=1,max_length=256)
    phase: Literal['started','completed']
    nativeItemDigest: str = Field(pattern=r'^[0-9a-f]{64}$')
    item: ActivityItem


def append(store, actor_id, room, operation, payload):
    payload=Observation.model_validate({**payload,'operationId':operation}).model_dump(mode='json',exclude={'operationId'})
    if len(json.dumps(payload).encode())>14000:raise ValueError('Native observation exceeds limit')
    with store.pool.connection() as c:
        a=store.actor(c,actor_id)
        if a['kind']!='agent':raise Denied('Native agent observation required')
        store.room(c,a,room,'can_post')
        source=store.readable_event(c,a,payload['sourceEventId'])
        if (str(source['room'])!=room or str(a['id']) not in source['data'].get('conversationRecipients',[])):
            raise Denied('Observation requires original room input addressed to this agent')
        item=payload['item']
        if item['nativeType'] not in ('commandExecution','fileChange','mcpToolCall','dynamicToolCall','collabAgentToolCall','planUpdate'):
            raise ValueError('Only structured native work is activity; messages are deliberate publications')
        if (payload['phase']=='started')!=(item['status']=='inProgress'):
            raise ValueError('Native phase and status disagree')
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(a['id'])+':'+operation,))
        fingerprint=digest({'roomId':room,**payload})
        old=c.execute('SELECT * FROM hb_command WHERE principal=%s AND operation=%s',(a['id'],operation)).fetchone()
        if old:
            if old['digest']!=fingerprint:raise Conflict('Observation identity changed')
            return store.receipt(c,old['event'])
        audience=source['audience']
        recipient=source['author'] if audience=='direct' else None
        action='action-'+digest([str(a['authority']),str(a['id']),payload['nativeThread'],payload['nativeTurn'],payload['itemId']])
        data={'type':'executionActivity','activityVersion':1,'conversationVersion':1,
              'actionId':action,'agentId':str(a['id']),'thread':payload['nativeThread'],
              'turn':payload['nativeTurn'],'itemId':payload['itemId'],'phase':payload['phase'],
              'nativeItemDigest':payload['nativeItemDigest'],'attestation':'native-agent',**item}
        # Private peer input never leaks to its room or unrelated observers.
        event=store.event(c,a,room,'progress',item['title'],source=source['id'],audience=audience,recipient=recipient,data=data)
        result=store.receipt(c,event)
        assert result['deliveries']==[]
        c.execute('INSERT INTO hb_command(authority,principal,operation,digest,event,accepted_result) VALUES(%s,%s,%s,%s,%s,%s)',
                  (a['authority'],a['id'],operation,fingerprint,event,Jsonb(result)))
        return json_value(result)
