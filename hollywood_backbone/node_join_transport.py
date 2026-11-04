"""Attach a joined Global's actual birth at the canonical room over NATS.

A lost result is inspected by the original operation ID; no alternate mutation,
new room, service/model start or synthetic user input is permitted.
"""
import argparse,asyncio,json,os
from pathlib import Path
from uuid import UUID,uuid5
from hollywood_backbone.store import Store,digest
from hollywood_backbone.federation_runtime import load
from hollywood_backbone.backplane import connect_host
from hollywood_backbone.federation_service import Client


async def attach_origin(profile,database,federation,operation_dir):
    if os.geteuid()!=0 or profile.get('schema')!='hollywood-node-join/v1':
        raise PermissionError('Original private joining-node operator profile required')
    store=Store(database);store.local_node_id=profile['nodeId']
    try:
        agent=profile['global']['id'];owner=profile['owner']['id']
        principal=store.authenticate(profile['global']['token'])
        role=store.role_read(owner,agent)
        descriptor=store.global_origin_read(owner,agent)
        config,password=load(federation)
        if (principal['id'],principal['authority'],principal['home_node'],config['hostId'])!=(agent,profile['authorityId'],profile['nodeId'],profile['nodeId']):
            raise PermissionError('Original authenticated local host and Global differ')
    finally:store.close()
    root=Path(operation_dir)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    operation='node-global-origin:'+profile['nodeId']
    binding={'authorityId':profile['authorityId'],'nodeId':profile['nodeId'],'agentId':agent,
             'targetNodeId':profile['globalOrigin']['ownerNodeId'],'profileSha256':digest(profile),
             'operationId':operation,'descriptor':descriptor}
    intent=root/'intent.private.json';accepted=root/'accepted.private.json'
    original=intent.exists()
    if original:
        if json.loads(intent.read_text())!=binding:raise ValueError('Original origin attachment changed')
    else:
        with intent.open('x') as f:json.dump(binding,f,indent=2);f.flush();os.fsync(f.fileno())
    if accepted.exists():return json.loads(accepted.read_text())
    connection=await connect_host(server=config['servers'],host_id=config['hostId'],password=password,ca_file=config['caFile'])
    try:
        client=Client(connection,authority_id=profile['authorityId'],host_id=profile['nodeId'],target_node_id=binding['targetNodeId'])
        dispatch=root/'dispatch-intent.private.json'
        original=dispatch.exists()
        if not original:
            with dispatch.open('x') as f:json.dump({'operationId':operation,'targetNodeId':binding['targetNodeId']},f);f.flush();os.fsync(f.fileno())
        action='global_origin_reference_read' if original else 'global_origin_attach'
        verb='GET' if original else 'POST'
        args=[operation] if original else [operation,descriptor]
        payload={'actorId':agent,'credentialDigest':digest(profile['global']['token']),
            'homeNodeId':profile['nodeId'],'homeRevision':principal['home_revision'],'homeRoleRevision':role['revision'],
            'action':action,'verb':verb,'path':'agents/'+agent+'/origin-reference',
            'body':None if original else {'operationId':operation,'descriptor':descriptor},'args':args,'nativeSource':None}
        result=await client.request('hollywood.agent.domain',payload,
            request_id=str(uuid5(UUID(profile['authorityId']),action+':'+operation)),timeout=15)
        if result.get('status')!=200:
            (root/'last-inspection.private.json').write_text(json.dumps(result,indent=2))
            raise RuntimeError('Original Global attachment remains unresolved: '+str(result.get('body',{}).get('error')))
        if result['body'].get('agentId')!=agent or result['body'].get('originRoomId')!=profile['globalOrigin']['room']['id']:
            raise RuntimeError('Original Global attachment receipt differs')
        with accepted.open('x') as f:json.dump(result['body'],f,indent=2);f.flush();os.fsync(f.fileno())
        return result['body']
    finally:await connection.close()


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('profile','database','federation','operation'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    for path in [a.profile,a.database,a.federation]:
        i=path.lstat()
        if path.is_symlink() or i.st_uid!=0 or i.st_mode&0o077:raise PermissionError('Root-private original configuration required')
    result=asyncio.run(attach_origin(json.loads(a.profile.read_text()),json.loads(a.database.read_text()),a.federation,a.operation))
    print(json.dumps(result))

if __name__=='__main__':main()
