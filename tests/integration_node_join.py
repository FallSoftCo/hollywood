"""Two independent real PostgreSQL nodes; no runtime, model or service starts."""
import copy,json,os,secrets
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store, Conflict, Denied, digest, json_value
from hollywood_backbone.node_join import initialize
from hollywood_backbone.federation_identity import describe, install_reference, resolve_in_transaction
from hollywood_backbone.native_effects import transport


def main():
    os.umask(0o077)
    root=Path(os.environ['JOIN_EVIDENCE']);root.mkdir(mode=0o700)
    admin=json.loads(Path(os.environ['JOIN_DATABASE']).read_text())
    dbs=[];stores=[];trace={}
    uid=lambda:str(uuid4())
    try:
        for _ in range(2):
            name='node_join_'+uuid4().hex
            with psycopg.connect(**admin,autocommit=True) as c:
                c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            dbs.append(name)
            store=Store({**admin,'dbname':name});stores.append(store);store.migrate()
        source,dest=stores
        authority,node_a,node_b,room=uid(),uid(),uid(),uid()
        source.local_node_id=node_a;dest.local_node_id=node_b
        creds={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ('Owner','Global')}
        owner_a=creds['Owner']['principalId'];global_a=creds['Global']['principalId']
        source.bootstrap(authority,'Synthetic joining-node QA',{'nodeId':node_a,'globalRoomId':room,'credentials':creds})
        source.placement(owner_a,node_a,'Node A','a'*64,{'qa':True},global_a)
        source.room_default_set(owner_a,node_a,{'agentId':global_a,'enabled':True,'revision':0})
        with source.pool.connection() as c:
            original_room=json_value(c.execute('SELECT * FROM hb_room WHERE id=%s',(room,)).fetchone())
            host_a=json_value(c.execute('SELECT * FROM hb_host WHERE id=%s',(node_a,)).fetchone())
            # Retained historical handoff roster is an audit snapshot, not a
            # permanent block on onboarding a new approved environment.
            c.execute('INSERT INTO hb_global_room_participation VALUES(%s,%s,%s::uuid[],%s::uuid[],0,%s,%s)',
                (authority,room,[global_a],[],Jsonb([]),'0'*64))
        before=source.post(owner_a,room,'before-join',{'kind':'message','body':'Synthetic original input','audience':'room'})
        profile={'schema':'hollywood-node-join/v1','authorityId':authority,'authorityName':'Synthetic joining-node QA','nodeId':node_b,
            'owner':{'id':uid(),'name':'Owner','token':secrets.token_urlsafe(40)},
            'global':{'id':uid(),'name':'Global Node B','token':secrets.token_urlsafe(40),'purpose':'Operate Node B'},
            'host':{'id':node_b,'authority':authority,'name':'Node B','identity_fingerprint':'b'*64,'capabilities':{'qa':True}},
            'globalOrigin':{'room':original_room,'ownerNodeId':node_a,'ownerAgentReference':describe(source,owner_a,global_a),'ownerHost':host_a}}
        result=initialize(dest,profile);assert result['servicesOrModelsStarted']==0
        assert initialize(dest,profile)==result
        with dest.pool.connection() as c:
            rooms=c.execute('SELECT id,room_reference FROM hb_room').fetchall()
            assert len(rooms)==1 and str(rooms[0]['id'])==room and rooms[0]['room_reference']
            assert c.execute('SELECT count(*) AS n FROM hb_room_agent_initialization').fetchone()['n']==0
            assert c.execute('SELECT count(*) AS n FROM hb_room_default_agent').fetchone()['n']==0
        changed=copy.deepcopy(profile);changed['global']['token']=secrets.token_urlsafe(40)
        try:initialize(dest,changed)
        except Conflict:pass
        else:raise AssertionError('Credential replacement accepted')
        b=profile['global']['id'];owner_b=profile['owner']['id']
        peer=describe(dest,owner_b,b)
        with source.pool.connection() as c:
            h=profile['host'];c.execute('INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s)',
                (node_b,authority,h['name'],h['identity_fingerprint'],Jsonb(h['capabilities'])))
        install_reference(source,owner_a,peer,enroll_execution_reference=True)
        def participants():
            with source.pool.connection() as c:
                return sorted(str(x['principal']) for x in c.execute('SELECT principal FROM hb_room_current_participant WHERE room=%s',(room,)).fetchall())
        assert participants()==[global_a]
        source.room_default_set(owner_a,node_b,{'agentId':b,'enabled':True,'revision':0})
        assert participants()==[global_a] # No invented origin before home attach.
        descriptor=dest.global_origin_read(owner_b,b)
        with source.pool.connection() as c:
            admission=resolve_in_transaction(c,authority_id=authority,source_host=node_b,principal_id=b,home_node=node_b,credential_digest=peer['credentialDigest'])
        def attach(op):
            with transport(b,peer['credentialDigest'],'POST','global_origin_attach','agents/'+b+'/origin-reference',descriptor,None,admission=admission):
                return source.global_origin_attach(b,op,descriptor)
        attached=attach('original-join');assert attach('original-join')==attached
        assert participants()==sorted([global_a,b]),participants()
        after=source.post(owner_a,room,'after-join',{'kind':'message','body':'Synthetic subsequent input','audience':'room'})
        assert b in after['event']['data']['conversationRecipients']
        assert b not in before['event']['data']['conversationRecipients']
        assert source.post(owner_a,room,'before-join',{'kind':'message','body':'Synthetic original input','audience':'room'})==before
        source.membership(owner_a,room,b,{'can_read':False,'can_post':False,'can_request':False},1)
        assert participants()==[global_a]
        # Reconciliation may not reenable a deliberately disabled identity.
        with dest.pool.connection() as c:c.execute('UPDATE hb_principal SET active=false WHERE id=%s',(b,))
        try:initialize(dest,profile)
        except Conflict:pass
        else:raise AssertionError('Disabled original identity reenrolled')
        trace={'passed':True,'label':'Real two-node PostgreSQL domain qualification; not NATS/native/host enrollment proof',
            'singleCanonicalGlobalRoom':True,'originalReceiptRetained':True,'lateNodeReceivesOnlyNewInputs':True,
            'explicitReadRevocationRespected':True,'disabledIdentityAndChangedCredentialRejected':True,
            'servicesOrModelsStarted':0,'join':result}
    finally:
        for s in stores:s.close()
        for name in dbs:
            with psycopg.connect(**admin,autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        trace['ownedDatabasesRemoved']=len(dbs)
        (root/'result.private.json').write_text(json.dumps(trace,indent=2))
    print(json.dumps(trace))

if __name__=='__main__':main()
