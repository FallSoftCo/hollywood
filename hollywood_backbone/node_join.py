"""Owner-local enrollment into an existing organization, without a second room.

Unlike a new-organization bootstrap, a joining node imports the canonical Global
room as a reference and births its own Global there. No model/worker/service is
started here. Reference packets are explicit operator-verified enrollment data.
"""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID
from psycopg.types.json import Jsonb
from .store import Store, Conflict, Denied, digest, json_value
from .federation_identity import install_reference, install_room_reference


def initialize(store, profile):
    if os.geteuid()!=0 or profile.get('schema')!='hollywood-node-join/v1':
        raise PermissionError('Verified local operator and exact node join profile required')
    authority,node=[str(UUID(profile[k])) for k in ('authorityId','nodeId')]
    owner=profile['owner'];agent=profile['global'];origin=profile['globalOrigin']
    owner_id,agent_id=[str(UUID(x['id'])) for x in (owner,agent)]
    if owner_id==agent_id or origin['room'].get('room_kind')!='global':
        raise ValueError('Distinct owner/agent and canonical typed Global origin required')
    host=profile['host']
    if (host['id'],host['authority'])!=(node,authority):
        raise ValueError('Original host/organization identity differs')
    # Fresh namespace birth only. Original active/disabled identities and keys
    # are compared, never overwritten, rotated or implicitly reenabled.
    with store.pool.connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('join:'+authority+':'+node,))
        existing=c.execute('SELECT name FROM hb_authority WHERE id=%s',(authority,)).fetchone()
        if existing and existing['name']!=profile['authorityName']:
            raise Conflict('Existing organization name differs')
        c.execute('INSERT INTO hb_authority(id,name) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING',
                  (authority,profile['authorityName']))
        for entry,kind in ((owner,'human'),(agent,'agent')):
            identity=str(UUID(entry['id']))
            old=c.execute('SELECT * FROM hb_principal WHERE id=%s FOR UPDATE',(identity,)).fetchone()
            if old and (str(old['authority']),old['name'],old['kind'],old['administrator'],old['active'],
                        str(old['home_node']),old['identity_reference']) != (
                        authority,entry['name'],kind,True,True,node,False):
                raise Conflict('Original local join identity differs or is deliberately disabled')
            if not old:
                c.execute('INSERT INTO hb_principal(id,authority,name,kind,administrator,home_node) VALUES(%s,%s,%s,%s,true,%s)',
                          (identity,authority,entry['name'],kind,node))
                c.execute('INSERT INTO hb_token VALUES(%s,%s,false)',(digest(entry['token']),identity))
            elif not c.execute('SELECT 1 FROM hb_token WHERE principal=%s AND digest=%s AND NOT revoked',
                                (identity,digest(entry['token']))).fetchone():
                raise Conflict('Original node enrollment credential differs')
    store.placement(owner_id,node,host['name'],host['identity_fingerprint'],host['capabilities'],agent_id)
    peer=origin['ownerAgentReference'];peer_host=origin['ownerHost']
    if (str(peer['principal']['id'])!=origin['room']['owner']
            or peer_host['id']!=origin['ownerNodeId'] or peer_host['authority']!=authority):
        raise ValueError('Canonical room owner reference differs')
    # Explicit receiving host metadata is required before installing a peer.
    with store.pool.connection() as c:
        old=c.execute('SELECT * FROM hb_host WHERE id=%s FOR UPDATE',(peer_host['id'],)).fetchone()
        if old and json_value(old)!=peer_host:raise Conflict('Original peer host metadata changed')
        if not old:
            c.execute('INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s)',
                (peer_host['id'],authority,peer_host['name'],peer_host['identity_fingerprint'],Jsonb(peer_host['capabilities'])))
    install_reference(store,owner_id,peer,enroll_execution_reference=True)
    install_room_reference(store,owner_id,origin['room'],owner_node=origin['ownerNodeId'])
    # The canonical room is remote. Birth the local responsibility after the
    # verified reference exists; ordinary role edits rightly require LOCAL
    # room authority and must not be weakened to perform this installation.
    with store.pool.connection() as c:
        old=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s FOR UPDATE',(agent_id,)).fetchone()
        if old and (old['primary_role'],old['purpose'],str(old['home_room']),
                    old['native_goal_scope'],str(old['decision_owner']),old['revision']) != (
                    'global',agent['purpose'],origin['room']['id'],'role',owner_id,1):
            raise Conflict('Original Global responsibility differs; no reassignment')
        if not old:
            c.execute("""INSERT INTO hb_agent_role(principal,authority,primary_role,purpose,
                home_room,native_goal_scope,decision_owner) VALUES(%s,%s,'global',%s,%s,'role',%s)""",
                (agent_id,authority,agent['purpose'],origin['room']['id'],owner_id))
    # Record actual first origin before native context creation. This does not
    # reclassify or copy a prior context, create goals, or enroll room workers.
    with store.pool.connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('join:'+authority+':'+node,))
        role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE',(agent_id,)).fetchone()
        principal=c.execute('SELECT * FROM hb_principal WHERE id=%s FOR SHARE',(agent_id,)).fetchone()
        manifest={'roomId':origin['room']['id'],'roomRevision':origin['room']['revision'],
                  'ownerId':origin['room']['owner'],'homeNodeId':node,'homeRevision':principal['home_revision'],
                  'roleRevision':role['revision'],'priorHomeRoomId':str(role['home_room']),
                  'reviewSha256':digest(profile),'birthVersion':1}
        old=c.execute('SELECT * FROM hb_global_origin WHERE agent=%s FOR UPDATE',(agent_id,)).fetchone()
        if old and (old['kind']!='bootstrap' or old['manifest']!=manifest):
            raise Conflict('Original Global birth differs')
        if not old:
            c.execute('INSERT INTO hb_global_origin VALUES(%s,%s,%s,%s,%s,%s,%s)',
                      (authority,agent_id,origin['room']['id'],node,'bootstrap',Jsonb(manifest),Jsonb(json_value(role))))
    return {'schema':'hollywood-node-join/v1','state':'metadata-ready','authorityId':authority,
            'nodeId':node,'agentId':agent_id,'originRoomId':origin['room']['id'],
            'duplicateRoomCreated':False,'servicesOrModelsStarted':0,'defaultParticipationEnabled':False}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    for path in (args.profile,args.database):
        info=path.lstat()
        if path.is_symlink() or info.st_uid!=0 or info.st_mode&0o077:
            raise PermissionError('Root-private, non-symlink installation files required')
    profile=json.loads(args.profile.read_text())
    # The profile itself is the immutable intent. A previous receipt is inspected
    # against the same namespace rather than replaying any model/runtime start.
    store=Store(json.loads(args.database.read_text()))
    try:
        store.migrate()
        result=initialize(store,profile)
        if args.receipt.exists() and json.loads(args.receipt.read_text())!=result:
            raise Conflict('Original metadata receipt differs')
        if not args.receipt.exists():
            with args.receipt.open('x') as f:json.dump(result,f,indent=2);f.flush();os.fsync(f.fileno())
        print(json.dumps(result))
    finally:store.close()


if __name__=='__main__':main()
