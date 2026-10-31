"""Expiring native status observations. This table never admits or starts work."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from psycopg.types.json import Jsonb
from .activity_models import Input
from .store import Conflict,Denied,digest,json_value

SCHEMA='''CREATE TABLE IF NOT EXISTS hb_conversation_state (
 authority UUID NOT NULL REFERENCES hb_authority(id),
 room UUID NOT NULL REFERENCES hb_room(id), agent UUID NOT NULL REFERENCES hb_principal(id),
 native_thread TEXT NOT NULL, revision BIGINT NOT NULL, fingerprint TEXT NOT NULL,
 source UUID REFERENCES hb_event(id), state JSONB NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
 PRIMARY KEY(room,agent,native_thread)
);'''

class NativeStatus(Input):
    type: Literal['notLoaded','idle','systemError','active','unavailable']
    activeFlags: list[Literal['waitingOnApproval','waitingOnUserInput']] = Field(default_factory=list,max_length=2)

class Observation(Input):
    observationType: Literal['nativeState']
    nativeThread: str = Field(min_length=1,max_length=128)
    revision: int = Field(strict=True,ge=1,le=9007199254740991)
    sourceEventId: UUID | None = None
    status: NativeStatus


def observe(store,actor_id,room,payload):
    payload=Observation.model_validate(payload).model_dump(mode='json')
    status=payload['status']
    if status['type']!='active' and status['activeFlags']:raise ValueError('Only active threads have waiting flags')
    with store.pool.connection() as c:
        a=store.actor(c,actor_id)
        if a['kind']!='agent':raise Denied('Native agent observation required')
        store.room(c,a,room,'can_post')
        source=payload['sourceEventId']
        if source:
            event=store.readable_event(c,a,source)
            if str(event['room'])!=room or str(a['id']) not in event['data'].get('conversationRecipients',[]):
                raise Denied('Native status source does not belong to this room agent')
        elif status['type']=='active':raise ValueError('Active room work requires its original input')
        identity=[room,str(a['id']),payload['nativeThread']]
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('native-status:'+digest(identity),))
        old=c.execute('SELECT * FROM hb_conversation_state WHERE room=%s AND agent=%s AND native_thread=%s',identity).fetchone()
        fingerprint=digest(payload)
        if old and (old['revision']>payload['revision'] or old['revision']==payload['revision'] and old['fingerprint']!=fingerprint):
            raise Conflict('Native status revision is stale or changed')
        if not old or old['revision']<payload['revision']:
            c.execute('''INSERT INTO hb_conversation_state(authority,room,agent,native_thread,revision,fingerprint,source,state)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(room,agent,native_thread) DO UPDATE SET
                revision=EXCLUDED.revision,fingerprint=EXCLUDED.fingerprint,source=EXCLUDED.source,
                state=EXCLUDED.state,observed_at=statement_timestamp()''',
                (a['authority'],room,a['id'],payload['nativeThread'],payload['revision'],fingerprint,source,Jsonb(status)))
        row=c.execute('SELECT revision,observed_at FROM hb_conversation_state WHERE room=%s AND agent=%s AND native_thread=%s',identity).fetchone()
        return json_value({'accepted':True,'observationType':'nativeState','authorityId':str(a['authority']),
            'roomId':room,'agentId':str(a['id']),'nativeThread':payload['nativeThread'],**row})
