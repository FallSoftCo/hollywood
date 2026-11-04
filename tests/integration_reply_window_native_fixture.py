"""Owned private QA API, independent qualification database; no model workers."""
import asyncio,json,os,secrets,signal
from pathlib import Path
from uuid import uuid4
from aiohttp import web
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

async def main():
    os.umask(0o077)
    root=Path(os.environ['MODEL_REPLY_ROOT']);root.mkdir(mode=0o700,exist_ok=True)
    store=Store(json.load(open(os.environ['REPLY_DATABASE'])))
    uid=lambda:str(uuid4())
    authority,room=uid(),uid()
    creds={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ('Owner','Global')}
    agents=[{'id':uid(),'token':secrets.token_urlsafe(40),'name':name,'fact':fact} for name,fact in [
        ('CPU peer A','OZZZ-OS room agents use gpt-6.1-sol at xhigh reasoning effort.'),
        ('CPU peer B','OZZZ-OS room agents use gpt-6.1-sol at xhigh reasoning effort.'),
        ('Native Mac peer','Native MacBook Pro room agents use gpt-6-astra at xhigh reasoning effort.')]]
    store.bootstrap(authority,'Synthetic answer comparison model QA',{'globalRoomId':uid(),'credentials':creds})
    owner=creds['Owner']['principalId']
    for a in agents:store.principal(owner,a['id'],a['name'],'agent',a['token'])
    store.create_room(owner,room,'Synthetic model reply comparison','No business actions or placements',agents[0]['id'])
    for a in agents:store.membership(owner,room,a['id'],{'can_read':True,'can_post':True,'can_request':True},0)
    # Pre-input fixture setup ONLY: model identities have no provisioned host,
    # so use the supported legacy-room member roster, not initialized fleet IDs.
    with store.pool.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM hb_room_agent_initialization WHERE room=%s',(room,)).fetchone()['n']==0
        c.execute('DELETE FROM hb_room_agent_generation WHERE room=%s',(room,))
    source=store.post(owner,room,'original-model-question',{'kind':'message','body':'Which model and thinking level are the room agents using?','audience':'room'})['event']
    assert all(a['id'] in source['data']['conversationRecipients'] for a in agents)
    runner=web.AppRunner(application(store),access_log=None);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
    setup={'authority':authority,'room':room,'agents':agents,'source':source,'base':base}
    path=root/'setup.private.json';path.write_text(json.dumps(setup));os.chmod(path,0o600)
    import pwd
    user=pwd.getpwuid(int(os.environ['REPLY_NATIVE_UID']));os.chown(path,user.pw_uid,user.pw_gid)
    print(json.dumps({'authority':authority,'room':room,'base':base}),flush=True)
    done=asyncio.Event();asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
    try:await done.wait()
    finally:await runner.cleanup();store.pool.close()

asyncio.run(main())
