"""ONE frozen-byte real Windows journey: update, lost ACK, accepted rollback.

No product task control. Never rerun an existing qualification directory.
All uncertainty belongs to this exact task/operation; no replacement on failure.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile
import win32api
import win32con
import win32event
import win32process
import win32com.client
from lax_connector.windows_ipc import create_private_directory, private_path

ROOT = Path(__file__).resolve().parent
E = ROOT/'qualification-r02'
H = Path('D:/NextCompHost20261101r01')
R = Path('D:/NextCompNative20261101r01')
BOOT = R/'runtime-r02/boot.py'
PYTHON = Path(sys.executable)
PYTHONW = PYTHON.with_name('pythonw.exe')
NORMALIZER = R/'windows-host-tasks-r03.py'
spec = importlib.util.spec_from_file_location('adapter', ROOT/'api_component_windows.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def save(path, value):
    api.exclusive(path, value)


def sha(path):
    return api.digest(path.read_bytes())


def missing(error):
    return (getattr(error, 'hresult', None) == -2147024894 or
            getattr(error, 'excepinfo', None) and error.excepinfo[5] == -2147024894)


def snapshot():
    folder = api.scheduler()
    tasks = {}
    for role in ('api', 'global', 'native', 'postgres'):
        p = H/(role+'-task.private.json')
        spec = json.loads(p.read_text())
        task = folder.GetTask(spec['taskName'])
        tasks[role] = {'specSha256': sha(p), 'xmlSha256': api.digest(task.Xml.encode()),
            'aclSha256': api.digest(task.GetSecurityDescriptor(7).encode()), 'enabled': bool(task.Enabled)}
    ps = r"@(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in @(43616,13108,31396,22044,19904) } | Select-Object ProcessId,Name,ExecutablePath,CreationDate,SessionId) | ConvertTo-Json -Compress"
    processes = json.loads(subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],
        capture_output=True, text=True, timeout=15, check=True).stdout)
    files = {str(p): sha(p) for p in (H/'native-manifest.private.json', H/'global-config.private.json',
         R/'runtime-r02/hollywood.pyz', R/'runtime-r02/runtime-manifest.json')}
    return {'tasks': tasks, 'processes': processes, 'files': files}


def held(call):
    try:
        call()
    except api.Held as error:
        return str(error)
    raise AssertionError('Expected held/no-dispatch result')


def boundary(operation, name, overrides=None):
    plan = api.load(operation)
    value = {'schema':'hollywood-api-quiet-boundary/v1','approved':True,
        'component':api.QA_TASK,'operation':str(operation),'approvedAt':time.time(),
        'originalXmlSha256':plan['original']['xmlSha256'],
        'planSha256':sha(operation/'plan.json'),'successorXmlSha256':plan['successorXmlSha256']}
    value.update(overrides or {})
    path = E/(name+'.json')
    save(path, value)
    return path, sha(path)


def metadata_launcher(package):
    return ('import ctypes,json,os,pathlib,sys,zipfile\nfrom ctypes import wintypes\n'
        f'package=pathlib.Path({str(package)!r})\n'
        'with zipfile.ZipFile(package) as z: marker=z.read("marker.txt").decode()\n'
        'pathlib.Path(sys.argv[1]).write_text(json.dumps({"pid":os.getpid(),"marker":marker,"package":str(package),"interpreter":sys.executable}))\n'
        'k=ctypes.WinDLL("kernel32",use_last_error=True)\n'
        'k.OpenEventW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR];k.OpenEventW.restype=wintypes.HANDLE\n'
        'k.SetEvent.argtypes=[wintypes.HANDLE];k.CloseHandle.argtypes=[wintypes.HANDLE]\n'
        'k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]\n'
        'ready=k.OpenEventW(2,False,sys.argv[2]);release=k.OpenEventW(0x100000,False,sys.argv[3]);assert ready and release\n'
        'assert k.SetEvent(ready);assert k.WaitForSingleObject(release,8000)==0\n'
        'k.CloseHandle(ready);k.CloseHandle(release)\n')


def pins(directory):
    return {'launcher':str(directory/'launcher.py'),'launcherSha256':sha(directory/'launcher.py'),
            'package':str(directory/'fixture.pyz'),'packageSha256':sha(directory/'fixture.pyz')}


def live_pins(spec):
    task = api.scheduler().GetTask(api.QA_TASK)
    return {**spec,'liveXmlSha256':api.digest(task.Xml.encode()),
        'taskAclSha256':api.digest(task.GetSecurityDescriptor(7).encode())}


def run():
    assert not E.exists(), 'Inspect the original frozen journey/task; no repeat or replacement'
    private_path(ROOT, directory=True)
    source_hashes = {n:sha(ROOT/n) for n in ('api_component_windows.py','test_api_component_windows.py','API_COMPONENTS_WINDOWS.md')}
    create_private_directory(E)
    save(E/'frozen-source.json', {'sourceEvent':'ffb5ded6-1546-44b0-81cc-28967eb9e4fe',
        'files':source_hashes,'at':time.time(),'budgetBytes':4*1024*1024})
    prior = H/'work/windows-api-component-39f94f8f-r01/qualification-r01/qa-cleanup-accepted.json'
    prior_cleanup = json.loads(prior.read_text())
    assert prior_cleanup['deleted'] is True and prior_cleanup['processTerminal'] is True
    folder = api.scheduler()
    try:
        folder.GetTask(api.QA_TASK)
    except Exception as error:
        assert missing(error)
    else:
        raise AssertionError('Prior exact QA task is still present; do not replace it')
    save(E/'prior-task-absent.json', {'taskName':api.QA_TASK,'priorCleanupReceiptSha256':sha(prior),
        'exactComNotFound':True,'at':time.time()})
    before = snapshot()
    save(E/'product-before.private.json', before)
    for name, marker in (('original','original-v1'),('successor','successor-v2'),('rollback','original-v1')):
        root = E/name
        create_private_directory(root)
        with zipfile.ZipFile(root/'fixture.pyz','x',zipfile.ZIP_DEFLATED) as z:
            z.writestr('marker.txt',marker)
        api.exclusive(root/'launcher.py', metadata_launcher(root/'fixture.pyz').encode())
    create_private_directory(E/'records')
    output = E/'metadata.json'
    ready_name, release_name = 'Local\\LaxApiQA39R02Ready','Local\\LaxApiQA39R02Release'
    original_command = [str(PYTHONW),'-I','-S','-B',str(E/'original/launcher.py'),str(output),ready_name,release_name]
    original = {'schema':'lax-windows-task/v1','role':'hollywood','taskName':api.QA_TASK,
        'command':original_command,'cwd':str(E),'recordDirectory':str(E/'records')}
    save(E/'original-spec.private.json',original)
    service = win32com.client.Dispatch('Schedule.Service');service.Connect()
    d = service.NewTask(0)
    d.RegistrationInfo.Description = 'Finite frozen Windows API pin-chain QA; no product server/model'
    d.Principal.UserId=api.current_owner();d.Principal.LogonType=3;d.Principal.RunLevel=0
    d.Settings.Enabled=False;d.Settings.Hidden=True;d.Settings.MultipleInstances=2
    d.Settings.ExecutionTimeLimit='PT1M';d.Settings.DisallowStartIfOnBatteries=False
    d.Settings.StopIfGoingOnBatteries=False;d.Settings.StartWhenAvailable=False
    a=d.Actions.Create(0);a.Path=str(PYTHONW);a.Arguments=subprocess.list2cmdline(original_command[1:]);a.WorkingDirectory=str(E)
    assert d.Triggers.Count==0
    save(E/'qa-registration-intent.json',{'taskName':api.QA_TASK,'at':time.time(),'noTriggers':True,'enabled':False})
    task=folder.RegisterTaskDefinition(api.QA_TASK,d,2|16|32,api.current_owner(),None,3)
    save(E/'qa-registration-accepted.json',{'taskName':api.QA_TASK,'xmlSha256':api.digest(task.Xml.encode()),'at':time.time()})
    installed={'taskName':api.QA_TASK,'specSha256':api.digest(api.canonical(original)),'xmlSha256':api.digest(task.Xml.encode())}
    save(E/'records/installation.json',installed)
    original_acceptance = sha(E/'records/installation.json')
    spec={'schema':'hollywood-api-component-windows/v1','component':api.QA_TASK,'normalizer':str(NORMALIZER),
        'originalSpecFile':str(E/'original-spec.private.json'),'originalSpecSha256':installed['specSha256'],
        'installationFile':str(E/'records/installation.json'),'installationSha256':original_acceptance,
        'interpreterSha256':sha(PYTHONW),'dependencyPins':[{'path':str(BOOT),'sha256':sha(BOOT)}],
        'from':pins(E/'original'),'to':pins(E/'successor'),'predecessor':None}
    spec=live_pins(spec)
    first, competitor=E/'first-update',E/'competitor'
    original_live=task.Xml
    assert api.prepare(spec,first)['state']=='prepared'
    assert api.prepare(spec,competitor)['state']=='prepared'
    assert folder.GetTask(api.QA_TASK).Xml==original_live
    for label, override in (('bad-plan',{'planSha256':'0'*64}),('bad-successor',{'successorXmlSha256':'0'*64})):
        b,h=boundary(first,label,override)
        held(lambda:api.commit(first,b,h))
        assert not (first/'registration-intent.json').exists()
        assert folder.GetTask(api.QA_TASK).Xml==original_live
    save(E/'quiet-negatives.json',{'wrongPlanHeld':True,'wrongSuccessorHeld':True,'managerDispatches':0,'originalXmlUnchanged':True})
    b1,h1=boundary(first,'first-quiet');bc,hc=boundary(competitor,'competitor-quiet')
    native_register=api._register
    calls=[]
    def lose_ack(folder,name,definition,owner,sddl):
        child=subprocess.run([str(PYTHON),'-I','-S','-B','-X','utf8',str(BOOT),'--script',str(Path(__file__)),
            '--contender','--operation',str(competitor),'--boundary',str(bc),'--sha256',hc],
            capture_output=True,text=True,timeout=20)
        assert child.returncode==0,child.stderr
        assert json.loads(child.stdout)['held']
        assert not (competitor/'registration-intent.json').exists()
        save(E/'kernel-barrier.json',{'childExit':0,'held':True,'competingIntentAbsent':True})
        save(E/'first-actual-registration-call.json',{'at':time.time(),'component':name})
        calls.append('first')
        result=native_register(folder,name,definition,owner,sddl)
        save(E/'first-actual-readback.json',{'xmlSha256':api.digest(result.Xml.encode()),'enabled':bool(result.Enabled)})
        raise OSError('Controlled lost ACK AFTER actual COM registration/readback')
    api._register=lose_ack
    try:
        reason=held(lambda:api.commit(first,b1,h1))
        assert 'unknown' in reason and calls==['first']
    finally:
        api._register=native_register
    assert api.inspect(first)['state']=='successor-definition'
    assert not (first/'registration-accepted.json').exists()
    pending=copy.deepcopy(spec)
    pending.update({'from':spec['to'],'to':pins(E/'rollback'),
        'predecessor':{'operation':str(first),'planSha256':sha(first/'plan.json'),'acceptanceSha256':'0'*64}})
    held(lambda:api.prepare(live_pins(pending),E/'unknown-predecessor'))
    assert not (E/'unknown-predecessor').exists()
    recovered=api.commit(first,b1,h1)
    assert recovered['reconciled'] and recovered['enabled'] is False
    accepted=sha(first/'registration-accepted.json')
    assert api.commit(first,b1,h1)['reconciled']
    assert accepted==sha(first/'registration-accepted.json') and calls==['first']
    held(lambda:api.commit(competitor,bc,hc))
    assert not (competitor/'registration-intent.json').exists()
    rollback=copy.deepcopy(pending)
    rollback['predecessor']['acceptanceSha256']=accepted
    rollback=live_pins(rollback)
    stale=copy.deepcopy(rollback);stale['predecessor']['planSha256']='0'*64
    held(lambda:api.prepare(stale,E/'stale-predecessor'))
    assert not (E/'stale-predecessor').exists()
    second=E/'accepted-rollback'
    assert api.prepare(rollback,second)['state']=='prepared'
    plan=api.load(second)
    assert plan['acceptedCommand'][0:4]==original_command[0:4]
    assert plan['acceptedCommand'][4]==spec['to']['launcher']
    assert plan['successorCommand'][4]==str(E/'rollback/launcher.py')
    b2,h2=boundary(second,'rollback-quiet')
    def track(folder,name,definition,owner,sddl):
        calls.append('rollback')
        return native_register(folder,name,definition,owner,sddl)
    api._register=track
    try:
        committed=api.commit(second,b2,h2)
    finally:
        api._register=native_register
    assert committed['state']=='successor-definition' and committed['enabled'] is False
    assert calls==['first','rollback'] and api.inspect(second)['state']=='successor-definition'
    held(lambda:api.commit(first,b1,h1))  # Original now deliberately rolled back; no replay.
    assert api.commit(second,b2,h2)['reconciled'] and calls==['first','rollback']
    assert sha(E/'records/installation.json')==original_acceptance
    assert json.loads((E/'original-spec.private.json').read_text())==original
    save(E/'chain-journey.json',{'firstUpdate':True,'acceptedRollbackOnSameTask':True,
        'actualSuccessorRegistrations':len(calls),'lostAckNoRedispatch':True,'staleAndUnknownPredecessorsHeld':True,
        'originalAfterRollbackHeld':True,'quietPlanAndSuccessorNegativesNoDispatch':True,
        'disabledPolicyPreserved':True,'originalAcceptanceImmutable':True})
    ready=win32event.CreateEvent(None,True,False,ready_name)
    release=win32event.CreateEvent(None,True,False,release_name)
    save(E/'qa-activation-intent.json',{'taskName':api.QA_TASK,'explicitQaEnableOnly':True,'oneFiniteRun':True,'at':time.time()})
    task=folder.GetTask(api.QA_TASK);task.Enabled=True
    instance=task.Run(None)
    save(E/'qa-start-accepted.json',{'instanceGuid':instance.InstanceGuid,'at':time.time()})
    assert win32event.WaitForSingleObject(ready,15000)==win32event.WAIT_OBJECT_0
    metadata=json.loads(output.read_text())
    process=win32api.OpenProcess(win32con.SYNCHRONIZE|win32con.PROCESS_QUERY_LIMITED_INFORMATION,False,metadata['pid'])
    win32event.SetEvent(release)
    try:
        assert win32event.WaitForSingleObject(process,10000)==win32event.WAIT_OBJECT_0
        assert win32process.GetExitCodeProcess(process)==0
    finally:
        process.Close();ready.Close();release.Close()
    assert metadata['marker']=='original-v1' and Path(metadata['package'])==E/'rollback/fixture.pyz'
    assert Path(metadata['interpreter'])==PYTHONW
    held(lambda:api.inspect(second))  # Explicit QA enable drift is not silently adopted.
    instance.Refresh()
    task=folder.GetTask(api.QA_TASK)
    assert task.GetInstances(0).Count==0, 'Terminal instance not yet reconciled; retain original task, no start retry'
    save(E/'qa-cleanup-intent.json',{'taskName':api.QA_TASK,'processTerminal':True,'instances':0,'at':time.time()})
    task.Enabled=False;folder.DeleteTask(api.QA_TASK,0)
    try:
        folder.GetTask(api.QA_TASK)
    except Exception as error:
        assert missing(error)
    else:
        raise AssertionError('QA task still exists')
    save(E/'qa-cleanup-accepted.json',{'deleted':True,'finiteProcessTerminal':True,'noReplacementTask':True})
    after=snapshot();save(E/'product-after.private.json',after)
    assert before==after
    assert source_hashes=={n:sha(ROOT/n) for n in source_hashes}
    size=sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file())
    assert size<4*1024*1024
    result={'passed':True,'sourceEvent':'ffb5ded6-1546-44b0-81cc-28967eb9e4fe',
        'finalBytesFrozenBeforeJourney':True,'sourceHashes':source_hashes,'qaTaskDeleted':True,
        'productIdentitiesUnchanged':before==after,'actualComUpdates':len(calls),'finiteRuns':1,
        'closureApparentBytes':size,'budgetBytes':4*1024*1024,'continuousPeakMeasured':False,
        'limits':['No live product API cutover or graceful shutdown/network qualification.',
                  'Source/operation chain test on real disposable task, not product activation authorization.']}
    save(E/'result.json',result)
    print(json.dumps({'passed':True,'result':str(E/'result.json'),'sha256':sha(E/'result.json'),
        'actualComUpdates':2,'finiteRuns':1,'qaTaskDeleted':True,'sameProductIdentities':before==after}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--contender',action='store_true')
    p.add_argument('--operation');p.add_argument('--boundary');p.add_argument('--sha256');a=p.parse_args()
    if a.contender:
        try:
            api.commit(Path(a.operation),Path(a.boundary),a.sha256)
        except api.Held as error:
            assert 'lock' in str(error)
            print(json.dumps({'held':True}))
        else:
            raise AssertionError('Competing process entered held component lock')
    else:
        try:
            run()
        except BaseException:
            if E.exists() and not (E/'failure.json').exists():
                save(E/'failure.json',{'traceback':traceback.format_exc(),
                    'instruction':'Inspect exact original task/operation; no registration/start retry or replacement identity.'})
            raise
