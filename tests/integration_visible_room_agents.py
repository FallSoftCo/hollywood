"""Real HTTP/PostgreSQL member catalog visibility, without model execution."""
import asyncio,json,secrets,sys,os
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from aiohttp import ClientSession,web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.store import Store,digest
from hollywood_backbone.server import application
from hollywood_backbone.federation_catalog import Catalog

async def main(database,evidence):
    os.umask(0o077); evidence.mkdir(mode=0o700,exist_ok=False)
    original=json.loads(Path(database).read_text()); schema='qa_visible_agents_'+uuid4().hex
    with psycopg.connect(**original,autocommit=True) as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    db={**original,'options':original.get('options','')+' -c search_path='+schema}
    store=None; runner=None
    try:
        initialize(db,evidence/'a','Visible agents qualification')
        initialize(db,evidence/'b','Foreign authority qualification')
        intent=json.loads((evidence/'a/installation-intent.json').read_text())
        foreign=json.loads((evidence/'b/installation-intent.json').read_text())
        authority=intent['authorityId']; room=intent['globalRoomId']; global_id=intent['credentials']['Global']['principalId']
        human,peer,hidden,inactive,reference=[str(uuid4()) for _ in range(5)]
        human_token=secrets.token_urlsafe(40); node=str(uuid4()); store=Store(db)
        with store.pool.connection() as c:
            for identity,name,kind in ((human,'Member','human'),(peer,'Visible peer','agent'),(hidden,'Hidden unroomed','agent'),(inactive,'Inactive peer','agent'),(reference,'Foreign reference','agent')):
                c.execute('INSERT INTO hb_principal(id,authority,name,kind) VALUES(%s,%s,%s,%s)',(identity,authority,name,kind))
            c.execute('INSERT INTO hb_token VALUES(%s,%s,false)',(digest(human_token),human))
            for identity in (human,peer,inactive,reference):
                c.execute('INSERT INTO hb_membership(authority,room,principal) VALUES(%s,%s,%s)',(authority,room,identity))
            c.execute('UPDATE hb_principal SET active=false WHERE id=%s',(inactive,))
            c.execute('UPDATE hb_principal SET identity_reference=true,home_node=%s WHERE id=%s',(str(uuid4()),reference))
        catalog=Catalog(store,None,authority_id=authority,host_id=node,node_id=node,node_routing=True,local_subjects={},trusted_viewers={})
        runner=web.AppRunner(application(store,federated_catalog=catalog),access_log=None)
        await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
        url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
        async with ClientSession(headers={'Authorization':'Bearer '+human_token}) as session:
            async with session.get(url+'rooms') as r:
                value=await r.json(); assert r.status==200,(r.status,value)
            ids={a['id'] for a in value['agents']}
            assert ids=={global_id,peer},ids
            assert all(a['authority']==authority for a in value['agents'])
            assert not any(x in json.dumps(value) for x in (human_token,intent['credentials']['Global']['token'],foreign['credentials']['Global']['principalId']))
            async with session.get(url+'agents') as r:
                assert r.status==403,(r.status,await r.text())
            with store.pool.connection() as c:
                c.execute('UPDATE hb_membership SET can_read=false WHERE room=%s AND principal=%s',(room,peer))
            async with session.get(url+'rooms') as r:
                after=await r.json(); assert r.status==200
            assert {a['id'] for a in after['agents']}=={global_id}
            with store.pool.connection() as c:
                c.execute('UPDATE hb_membership SET can_read=false WHERE room=%s AND principal=%s',(room,human))
            async with session.get(url+'rooms') as r:
                revoked=await r.json(); assert r.status==200
            assert revoked['agents']==[] and revoked['rooms']==[]
        result={'passed':True,'actualHttpPostgres':True,'visibleAgents':2,'administratorInventoryStillDenied':True,'unroomedInactiveForeignAndReferenceExcluded':True,'peerAndViewerRevocationImmediate':True,'credentialsAbsent':True,'modelsStarted':0,'liveServicesChanged':False,'schemaDroppedInFinally':True}
        (evidence/'result.json').write_text(json.dumps(result,indent=2)); print(json.dumps(result))
    finally:
        if runner:await runner.cleanup()
        if store:store.close()
        with psycopg.connect(**original,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

if __name__=='__main__': asyncio.run(main(sys.argv[1],Path(sys.argv[2])))
