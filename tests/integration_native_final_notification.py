"""Authenticated HTTP/PG notification policy against retained real native final.
Negative variants are rolled-back metadata fixtures; no messages/models/FCM.
"""
import argparse,asyncio,contextlib,json,time
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

def main(a):
    assert not a.output.exists()
    root=Path('/var/lib/lax-kernel-incarnation-20261028-r355/r02')
    token=json.loads((root/'bounded-installed-qa-r11-r01/bootstrap/installation-intent.json').read_text())['credentials']['Owner']['token']
    identity='af30e33e-527b-46ce-a895-6276d529008c'
    with psycopg.connect(**json.loads(a.database.read_text()),row_factory=dict_row) as c:
        class Borrow:
            @contextlib.contextmanager
            def connection(self):
                with c.transaction(): yield c
        store=Store.__new__(Store);store.pool=Borrow()
        event=c.execute('SELECT * FROM hb_event WHERE id=%s',(identity,)).fetchone()
        delivery=c.execute('SELECT * FROM hb_delivery WHERE result_event=%s',(identity,)).fetchone()
        assert event['kind']=='message' and event['audience']=='room' and delivery['state']=='completed'
        async def run():
            checks=[]
            async with TestServer(application(store),host='127.0.0.1') as server:
                async with ClientSession() as client:
                    async def read(auth=token,status=200):
                        async with client.get(server.make_url('/hollywood/v3/inbox/events/'+identity),headers={'Authorization':'Bearer '+auth}) as r:
                            v=await r.json();assert r.status==status,(r.status,v);return v
                    first=await read()
                    if a.mode=='baseline':
                        assert first['hint']['notify'] is False
                        return {'status':'baseline_missing_human_room_final_notification','originalHint':first['hint'],'cases':[]}
                    assert first['hint']['notify'] and first['hint']['reason']=='request_result'
                    checks.append('retained_real_native_room_final_notifies_original_human')
                    again=await read();assert again==first;checks.append('repeat_read_same_event_revision_no_new_admission')
                    for state,cancel in [('running',False),('completed',True)]:
                        c.execute('UPDATE hb_delivery SET state=%s,cancellation_requested=%s WHERE id=%s',(state,cancel,delivery['id']))
                        h=(await read())['hint'];assert not h['notify'];checks.append('not_terminal_final_'+state+'_'+str(cancel))
                    c.execute('UPDATE hb_delivery SET state=%s,cancellation_requested=%s WHERE id=%s',(delivery['state'],delivery['cancellation_requested'],delivery['id']))
                    c.execute('UPDATE hb_delivery SET result_event=NULL WHERE id=%s',(delivery['id'],))
                    assert not (await read())['hint']['notify'];checks.append('intermediate_same_source_message_quiet')
                    c.execute('UPDATE hb_delivery SET result_event=%s WHERE id=%s',(event['id'],delivery['id']))
                    c.execute("UPDATE hb_event SET audience='direct',recipient=%s WHERE id=%s",(first['hint']['principalId'],identity))
                    assert (await read())['hint']['notify'];checks.append('direct_human_final_still_notifies')
                    c.execute("UPDATE hb_event SET audience='room',recipient=NULL WHERE id=%s",(identity,))
                    c.execute("UPDATE hb_event SET author=%s WHERE id=%s",(first['hint']['principalId'],event['source']))
                    assert (await read())['hint']['notify']
                    peer_source=c.execute("SELECT e.id FROM hb_event e JOIN hb_principal p ON p.id=e.author WHERE e.authority=%s AND e.kind='request' AND p.kind='agent' LIMIT 1",(event['authority'],)).fetchone()
                    assert peer_source
                    c.execute('UPDATE hb_event SET source=%s WHERE id=%s',(peer_source['id'],identity))
                    assert not (await read())['hint']['notify'];checks.append('delegated_agent_request_does_not_notify_human')
                    # Visibility is enforced by readable_event before hint calculation.
                    c.execute('UPDATE hb_event SET source=%s WHERE id=%s',(event['source'],identity))
                    c.execute("UPDATE hb_principal SET administrator=false WHERE id=%s",(first['hint']['principalId'],))
                    c.execute("UPDATE hb_membership SET can_read=false WHERE room=%s AND principal=%s",(event['room'],first['hint']['principalId']))
                    await read(status=403);checks.append('current_room_read_permission_still_required')
                    return {'status':'passed','originalHint':first['hint'],'cases':checks}
        proof=asyncio.run(run());c.rollback()
        assert c.execute('SELECT * FROM hb_event WHERE id=%s',(identity,)).fetchone()==event
        assert c.execute('SELECT * FROM hb_delivery WHERE id=%s',(delivery['id'],)).fetchone()==delivery
        proof.update(at=time.time(),rollbackVerified=True,originalEventDeliveryUnchanged=True,newEvents=0,newModels=0,phonePushProved=False)
        a.output.write_text(json.dumps(proof,default=str,indent=2));print(json.dumps(proof,default=str))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=['baseline','candidate'],required=True);main(p.parse_args())
