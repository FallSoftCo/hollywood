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
    type: Literal['notLoaded','idle','systemError','active','unavailable','activeElsewhere']
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
        store.room(c,a,room)
        c.execute('SELECT id FROM hb_room WHERE id=%s FOR SHARE',(room,))
        try:store.room(c,a,room,'can_post')
        except Denied:
            from .room_handoff import observation as original_observation
            if not original_observation(store,c,a,room,payload):raise
        source=payload['sourceEventId']
        if source:
            event=store.readable_event(c,a,source)
            if str(event['room'])!=room or str(a['id']) not in event['data'].get('conversationRecipients',[]):
                raise Denied('Native status source does not belong to this room agent')
        elif status['type']=='active':raise ValueError('Active room work requires its original input')
        if status['type']=='activeElsewhere' and source:
            raise ValueError('Shared-context activity must not disclose another source or claim this room work')
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


CATALOG_CTES = """
native_members AS (
                       SELECT n.* FROM hb_conversation_state n JOIN readable_rooms r ON r.id=n.room
                       JOIN hb_principal p ON p.id=n.agent AND p.active
                       JOIN hb_membership m ON m.room=n.room AND m.principal=n.agent AND m.can_read
                       LEFT JOIN hb_room_agent_generation g ON g.room=n.room
                       LEFT JOIN hb_event original_source ON original_source.id=n.source
                       WHERE (g.phase IS DISTINCT FROM 'staging' OR NOT EXISTS(
                           SELECT 1 FROM hb_room_agent_initialization i WHERE i.room=n.room AND i.agent=n.agent))
                       AND (g.phase IS DISTINCT FROM 'active' OR NOT(n.agent=ANY(g.retired)) OR (
                           n.state->>'type'='active'
                           AND n.observed_at > statement_timestamp()-interval '75 seconds'
                           AND original_source.room=n.room AND original_source.seq<=g.handoff_seq
                           AND original_source.data->'conversationRecipients' ? n.agent::text))
                   ), native_counts AS (
                       SELECT room,count(*) AS total FROM native_members GROUP BY room
                   ), native_visible AS (
                       SELECT n.room,jsonb_agg(jsonb_build_object(
                         'agentId',n.agent,'nativeThread',n.native_thread,'revision',n.revision,
                         'observedAt',floor(extract(epoch FROM n.observed_at)*1000)::bigint,
                         'expiresAt',floor(extract(epoch FROM n.observed_at + interval '75 seconds')*1000)::bigint,
                         'status',CASE WHEN n.observed_at > statement_timestamp()-interval '75 seconds'
                           THEN n.state ELSE jsonb_build_object('type','unavailable','activeFlags','[]'::jsonb) END
                       ) ORDER BY n.agent,n.native_thread) AS agents
                       FROM native_members n LEFT JOIN hb_event e ON e.id=n.source
                       WHERE n.source IS NULL OR %s OR e.audience='room' OR e.author=%s OR e.recipient=%s
                       GROUP BY n.room
                   )
"""
CATALOG_PROJECTION = """
jsonb_build_object(
                     'version',1,'capabilities',jsonb_build_object('sharedThreadStatus',1),
                     'observedAt',floor(extract(epoch FROM statement_timestamp())*1000)::bigint,
                     'runtimeCount',coalesce(nc.total,0),'agents',coalesce(nv.agents,'[]'::jsonb)
                   )
"""


def read_room(store,c,actor,room):
    store.room(c,actor,room)
    row=c.execute('WITH readable_rooms AS (SELECT id FROM hb_room WHERE id=%s AND authority=%s), '
        +CATALOG_CTES+' SELECT '+CATALOG_PROJECTION+' AS value FROM readable_rooms r '
        +'LEFT JOIN native_counts nc ON nc.room=r.id LEFT JOIN native_visible nv ON nv.room=r.id',
        (room,actor['authority'],actor['administrator'],actor['id'],actor['id'])).fetchone()
    return json_value(row['value'])
