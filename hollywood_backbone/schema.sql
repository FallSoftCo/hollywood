CREATE TABLE IF NOT EXISTS hb_schema (version integer PRIMARY KEY);
INSERT INTO hb_schema VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS hb_authority (
 id uuid PRIMARY KEY, name text NOT NULL, next_seq bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS hb_principal (
 id uuid PRIMARY KEY, authority uuid NOT NULL REFERENCES hb_authority(id),
 name text NOT NULL, kind text NOT NULL CHECK(kind IN ('human','agent')),
 administrator boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true,
 UNIQUE(authority,id), UNIQUE(authority,name)
);
CREATE TABLE IF NOT EXISTS hb_token (
 digest text PRIMARY KEY, principal uuid NOT NULL REFERENCES hb_principal(id),
 revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS hb_room (
 id uuid PRIMARY KEY, authority uuid NOT NULL REFERENCES hb_authority(id),
 name text NOT NULL, purpose text NOT NULL,
 owner uuid NOT NULL, revision bigint NOT NULL DEFAULT 1,
 UNIQUE(authority,id), UNIQUE(authority,name),
 FOREIGN KEY(authority,owner) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_membership (
 authority uuid NOT NULL, room uuid NOT NULL, principal uuid NOT NULL,
 can_read boolean NOT NULL DEFAULT true, can_post boolean NOT NULL DEFAULT false,
 can_request boolean NOT NULL DEFAULT false, revision bigint NOT NULL DEFAULT 1,
 PRIMARY KEY(room,principal),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_agent_role (
 principal uuid PRIMARY KEY, authority uuid NOT NULL,
 primary_role text NOT NULL, home_room uuid, purpose text NOT NULL DEFAULT '',
 revision bigint NOT NULL DEFAULT 1,
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,home_room) REFERENCES hb_room(authority,id)
);
INSERT INTO hb_agent_role(principal,authority,primary_role,home_room,purpose)
 SELECT p.id,p.authority,CASE WHEN p.administrator THEN 'global' ELSE 'worker' END,
 (SELECT r.id FROM hb_room r WHERE r.owner=p.id ORDER BY r.name LIMIT 1),
 CASE WHEN p.administrator THEN 'Enterprise coordination and administration' ELSE 'Own explicitly addressed work' END
 FROM hb_principal p WHERE p.kind='agent' ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS hb_host (
 id uuid PRIMARY KEY, authority uuid NOT NULL, name text NOT NULL,
 identity_fingerprint text NOT NULL, capabilities jsonb NOT NULL,
 UNIQUE(authority,id), UNIQUE(authority,name),
 FOREIGN KEY(authority) REFERENCES hb_authority(id)
);
CREATE TABLE IF NOT EXISTS hb_placement (
 authority uuid NOT NULL, agent uuid PRIMARY KEY, host uuid NOT NULL,
 enabled boolean NOT NULL DEFAULT true, generation bigint NOT NULL DEFAULT 1,
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_event (
 id uuid PRIMARY KEY, authority uuid NOT NULL, seq bigint NOT NULL,
 room uuid NOT NULL, author uuid NOT NULL, recipient uuid,
 kind text NOT NULL CHECK(kind IN ('message','request','progress','result','question','approval','control')),
 audience text NOT NULL CHECK(audience IN ('room','direct')),
 body text NOT NULL, source uuid, data jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(authority,id), UNIQUE(authority,seq),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,author) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,recipient) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,source) REFERENCES hb_event(authority,id),
 CHECK(audience='room' OR recipient IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS hb_room_history ON hb_event(room,seq);
CREATE TABLE IF NOT EXISTS hb_command (
 authority uuid NOT NULL, principal uuid NOT NULL, operation text NOT NULL,
 digest text NOT NULL, event uuid NOT NULL,
 PRIMARY KEY(principal,operation),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_delivery (
 id uuid PRIMARY KEY, authority uuid NOT NULL, event uuid NOT NULL,
 agent uuid NOT NULL, host uuid, generation bigint NOT NULL DEFAULT 0,
 claim_id text, native_thread text, native_turn text,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','claimed','running','completed','failed','unknown','cancel_requested','cancelled')),
 result_event uuid, revision bigint NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(event,agent), UNIQUE(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id),
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id),
 FOREIGN KEY(authority,result_event) REFERENCES hb_event(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_pending_delivery ON hb_delivery(agent,state,event);
CREATE TABLE IF NOT EXISTS hb_claim (
 agent uuid NOT NULL REFERENCES hb_principal(id), operation text NOT NULL,
 host uuid NOT NULL REFERENCES hb_host(id), delivery uuid REFERENCES hb_delivery(id),
 PRIMARY KEY(agent,operation)
);
CREATE TABLE IF NOT EXISTS hb_transition (
 delivery uuid NOT NULL REFERENCES hb_delivery(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL,
 PRIMARY KEY(delivery,operation)
);
CREATE TABLE IF NOT EXISTS hb_attention (
 id uuid PRIMARY KEY, authority uuid NOT NULL, delivery uuid NOT NULL,
 agent uuid NOT NULL, human uuid NOT NULL, room uuid NOT NULL, event uuid NOT NULL,
 generation bigint NOT NULL, claim_id text NOT NULL,
 native_thread text NOT NULL, native_turn text NOT NULL, native_slot text NOT NULL,
 method text NOT NULL, request jsonb NOT NULL, digest text NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','response_submitted','native_submitted','closed','expired','unknown')),
 response jsonb, response_author uuid, response_event uuid, revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(delivery,native_turn,native_slot), UNIQUE(authority,id),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id),
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,human) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id),
 FOREIGN KEY(authority,response_author) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,response_event) REFERENCES hb_event(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_attention_inbox ON hb_attention(human,state,created_at);
CREATE TABLE IF NOT EXISTS hb_attention_command (
 attention uuid NOT NULL REFERENCES hb_attention(id), principal uuid NOT NULL REFERENCES hb_principal(id),
 operation text NOT NULL, digest text NOT NULL, result jsonb NOT NULL,
 PRIMARY KEY(attention,principal,operation)
);
CREATE TABLE IF NOT EXISTS hb_activity (
 delivery uuid NOT NULL REFERENCES hb_delivery(id), generation bigint NOT NULL,
 native_thread text NOT NULL, native_turn text NOT NULL, item_id text NOT NULL,
 phase text NOT NULL CHECK(phase IN ('started','completed')), digest text NOT NULL,
 event uuid NOT NULL REFERENCES hb_event(id),
 PRIMARY KEY(delivery,generation,native_turn,item_id,phase)
);
