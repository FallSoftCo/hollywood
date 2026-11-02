"""Owner-local API component pin transitions; never reenroll or start work.

Prepare produces an independent successor definition/receipt while retaining
the original SDK/capsule and launch acceptance. Service-manager cutover remains
an explicit owning operation, NOT a side effect of installation or inspection.
"""
import argparse,hashlib,json,os,plistlib,re,stat,zipfile
from pathlib import Path,PurePosixPath
from xml.etree import ElementTree as ET


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read(path,expected=None,limit=1048576):
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path or path.is_symlink():
        raise ValueError('Component paths must be exact, absolute and unaliased')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
        raise ValueError('Component file type/size differs')
    if os.name!='nt' and (info.st_uid not in (0,os.geteuid()) or info.st_mode&0o022):
        raise PermissionError('Component file has an untrusted writer')
    value=path.read_bytes()
    if expected is not None and digest(value)!=expected:raise ValueError('Component pin differs')
    return value


def exclusive(path,value):
    raw=json.dumps(value,sort_keys=True,indent=2).encode()+b'\n' if isinstance(value,dict) else value
    with Path(path).open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if os.name!='nt':Path(path).chmod(0o600)


def definition(kind,raw,old,new):
    if old==new:raise ValueError('API successor needs a distinct immutable path')
    if kind=='systemd':
        if not all(re.fullmatch(r'[/A-Za-z0-9_.+:-]+',p) for p in (old,new)):
            raise ValueError('Systemd argument needs an unambiguous literal path')
        text=raw.decode();lines=text.splitlines(keepends=True)
        candidates=[i for i,line in enumerate(lines) if line.startswith('ExecStart=')]
        if len(candidates)!=1 or text.count(old)!=1 or old not in lines[candidates[0]].split():
            raise ValueError('Expected exactly one API ExecStart argument')
        lines[candidates[0]]=lines[candidates[0]].replace(old,new)
        return ''.join(lines).encode()
    if kind=='launchd':
        value=plistlib.loads(raw);args=value.get('ProgramArguments')
        if not isinstance(args,list) or args.count(old)!=1:raise ValueError('Expected one API program argument')
        value['ProgramArguments']=[new if arg==old else arg for arg in args]
        return plistlib.dumps(value,sort_keys=False)
    if kind=='windows-task':
        root=ET.fromstring(raw);nodes=[n for n in root.iter() if n.tag.rsplit('}',1)[-1] in ('Command','Arguments')]
        matches=[n for n in nodes if old in (n.text or '')]
        if len(matches)!=1 or matches[0].text.count(old)!=1:raise ValueError('Expected one API task argument')
        # Supplied successor launcher is separately pinned; no capsule file is
        # edited. Task principal, triggers, enabled/restart policy are retained.
        matches[0].text=matches[0].text.replace(old,new)
        return ET.tostring(root,encoding='utf-8',xml_declaration=True)
    raise ValueError('Unsupported local service-manager definition')


def prepare(spec,operation):
    if os.name=='nt':raise NotImplementedError('Windows live pin writes require the owning task/ACL adapter qualification')
    if spec.get('schema')!='hollywood-api-component/v1' or not re.fullmatch(r'[-A-Za-z0-9_.]{1,150}',spec['component']):
        raise ValueError('Invalid API component binding')
    if 'hollywood' not in spec['component'].lower():raise ValueError('Not a Hollywood API component')
    if spec['kind']=='systemd' and (Path(spec['definitionFile']).name!=spec['component'] or not spec['component'].endswith('.service')):
        raise ValueError('API unit identity differs')
    old,new=spec['from'],spec['to']
    before=read(spec['definitionFile'],spec['definitionSha256'])
    if spec['kind']=='launchd' and plistlib.loads(before).get('Label')!=spec['component']:
        raise ValueError('API launchd identity differs')
    read(old['launcher'],old['launcherSha256'],536870912)
    read(new['launcher'],new['launcherSha256'],536870912)
    package=read(new['package'],new['packageSha256'],536870912)
    # Verify code container without extraction, instance configuration or DBs.
    import io
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        entries=archive.infolist()
        if len(entries)>10000 or sum(e.file_size for e in entries)>536870912:raise ValueError('API container exceeds bounds')
        names=set()
        for entry in entries:
            path=PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or entry.filename in names or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('Unsafe API code container')
            names.add(entry.filename)
        if archive.testzip() is not None:raise ValueError('Corrupt API code container')
    after=definition(spec['kind'],before,old['launcher'],new['launcher'])
    operation=Path(operation)
    if not operation.is_absolute() or operation.resolve()!=operation:raise ValueError('Operation path is aliased')
    operation.mkdir(mode=0o700)  # Existing operations must be inspected, never replaced.
    exclusive(operation/'definition.original',before);exclusive(operation/'definition.successor',after)
    plan={'schema':spec['schema'],'spec':spec,'successorSha256':digest(after),
          'originalSha256':digest(before),'state':'prepared','activationDispatched':False}
    exclusive(operation/'plan.json',plan)
    return plan


def inspect(operation):
    operation=Path(operation)
    plan=json.loads(read(operation/'plan.json'))
    spec=plan['spec'];raw=read(spec['definitionFile'])
    read(spec['from']['launcher'],spec['from']['launcherSha256'],536870912)
    read(spec['to']['launcher'],spec['to']['launcherSha256'],536870912)
    read(spec['to']['package'],spec['to']['packageSha256'],536870912)
    observed=digest(raw)
    if observed==plan['originalSha256']:state='original-definition'
    elif observed==plan['successorSha256']:state='successor-definition'
    else:raise ValueError('Definition changed outside the original component operation')
    return {'state':state,'definitionSha256':observed,'activationDispatched':False,
            'serviceHealth':'not-observed','originalAcceptanceRetained':True}


def commit(operation):
    """Write the exact prepared definition only. NO reload/restart/install call."""
    if os.name=='nt':raise NotImplementedError('Windows live pin writes require the owning task/ACL adapter qualification')
    operation=Path(operation);plan=json.loads(read(operation/'plan.json'))
    observed=inspect(operation)
    if observed['state']=='successor-definition':return {**observed,'reconciled':True}
    if (operation/'definition-write-intent.json').exists():
        raise ValueError('Original write may have been rolled back; inspect, do not replay')
    raw=read(operation/'definition.successor',plan['successorSha256'])
    target=Path(plan['spec']['definitionFile']);info=target.stat()
    exclusive(operation/'definition-write-intent.json',{'originalSha256':plan['originalSha256'],
        'successorSha256':plan['successorSha256'],'activationDispatched':False})
    temporary=target.with_name(target.name+'.api-component-new')
    with temporary.open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if os.name!='nt':
        temporary.chmod(stat.S_IMODE(info.st_mode));os.chown(temporary,info.st_uid,info.st_gid)
    # A concurrent owner/definition change is a hold, not permission to overwrite.
    read(target,plan['originalSha256']);os.replace(temporary,target)
    if os.name!='nt':
        fd=os.open(target.parent,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    result={**inspect(operation),'reconciled':False}
    exclusive(operation/'definition-write-result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','inspect','commit'))
    parser.add_argument('--operation',type=Path,required=True)
    parser.add_argument('--spec',type=Path)
    args=parser.parse_args()
    if args.command=='prepare':result=prepare(json.loads(read(args.spec)),args.operation)
    else:result=(inspect if args.command=='inspect' else commit)(args.operation)
    print(json.dumps(result))


if __name__=='__main__':main()
