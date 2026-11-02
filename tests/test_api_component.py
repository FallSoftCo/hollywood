"""Actual definition/pin filesystem boundary, never a production service test."""
import copy,hashlib,json,os,plistlib,subprocess,sys,tempfile,unittest,zipfile,select
from pathlib import Path
from xml.etree import ElementTree as ET
from hollywood_backbone.api_component import prepare,commit,inspect,definition


class ComponentPin(unittest.TestCase):
    def test_successor_is_independent_and_original_acceptance_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'original.pyz';new=root/'successor.pyz';unit=root/'hollywood-fixture.service'
            for file,version in ((old,'172'),(new,'177')):
                with zipfile.ZipFile(file,'w') as archive:archive.writestr('__main__.py','print('+repr(version)+')\n')
                file.chmod(0o644)
            unit.write_text('[Unit]\nAfter=network.target\n[Service]\nUser=existing\nExecStart=/usr/bin/python3 -I '+str(old)+' backbone\nRestart=on-failure\n')
            original=unit.read_bytes();original_package=old.read_bytes();original_info=old.stat()
            acceptance=root/'original-installation.json';acceptance.write_text(json.dumps({'definitionSha256':hashlib.sha256(original).hexdigest()}))
            accepted=acceptance.read_bytes()
            spec={'schema':'hollywood-api-component/v1','component':'hollywood-fixture.service','kind':'systemd',
                  'definitionFile':str(unit),'definitionSha256':hashlib.sha256(original).hexdigest(),
                  'from':{'launcher':str(old),'launcherSha256':hashlib.sha256(original_package).hexdigest()},
                  'to':{'launcher':str(new),'launcherSha256':hashlib.sha256(new.read_bytes()).hexdigest(),
                        'package':str(new),'packageSha256':hashlib.sha256(new.read_bytes()).hexdigest()}}
            operation=root/'original-operation';prepare(spec,operation)
            self.assertEqual(unit.read_bytes(),original);self.assertEqual(inspect(operation)['state'],'original-definition')
            with self.assertRaises(FileExistsError):prepare(spec,operation)
            # This is a REAL child-process check of the prepared code, not exit
            # status alone. No credential/config or API server entry is involved.
            child=subprocess.run([sys.executable,'-I',str(new)],capture_output=True,text=True,timeout=5)
            self.assertEqual((child.returncode,child.stdout.strip()),(0,'177'))
            result=commit(operation);self.assertEqual(result['state'],'successor-definition')
            current=unit.stat();self.assertTrue(commit(operation)['reconciled']);self.assertEqual(unit.stat().st_ino,current.st_ino)
            self.assertEqual(unit.read_bytes(),original.replace(str(old).encode(),str(new).encode()))
            self.assertEqual((old.read_bytes(),old.stat().st_ino,old.stat().st_ctime_ns),
                             (original_package,original_info.st_ino,original_info.st_ctime_ns))
            self.assertEqual(acceptance.read_bytes(),accepted)
            self.assertFalse(result['activationDispatched']);self.assertEqual(result['serviceHealth'],'not-observed')
            with self.assertRaisesRegex(ValueError,'Component pin differs'):prepare(spec,operation)
            unit.write_bytes(original+b'# concurrent owner change\n')
            with self.assertRaises(ValueError):inspect(operation)

    def test_reject_altered_package_and_aliased_definition_before_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);code=root/'code.pyz';unit=root/'hollywood-fixture.service';unit.write_text('ExecStart=/usr/bin/python '+str(code)+'\n')
            with zipfile.ZipFile(code,'w') as archive:archive.writestr('../escaping.py','bad')
            h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            spec={'schema':'hollywood-api-component/v1','component':'hollywood-fixture.service','kind':'systemd',
                  'definitionFile':str(unit),'definitionSha256':h(unit),
                  'from':{'launcher':str(code),'launcherSha256':h(code)},
                  'to':{'launcher':str(code),'launcherSha256':h(code),'package':str(code),'packageSha256':h(code)}}
            with self.assertRaisesRegex(ValueError,'Unsafe API'):prepare(spec,root/'operation')
            self.assertFalse((root/'operation').exists())
            aliases=root/'aliases';aliases.mkdir();link=aliases/unit.name
            link.symlink_to(unit);spec['definitionFile']=str(link)
            with self.assertRaisesRegex(ValueError,'unaliased'):prepare(spec,root/'operation')

    def test_mac_and_windows_definition_transforms_preserve_other_semantics(self):
        old='/original/boot.py';new='/successor/boot.py'
        value={'Label':'co.fallsoft.hollywood.fixture','ProgramArguments':['/python','-I',old,'--role','hollywood'],
               'KeepAlive':True,'RunAtLoad':False,'EnvironmentVariables':{'UNRELATED':'preserved'}}
        changed=plistlib.loads(definition('launchd',plistlib.dumps(value),old,new))
        expected=copy.deepcopy(value);expected['ProgramArguments'][2]=new;self.assertEqual(changed,expected)
        xml=b'<Task><Principals><UserId>original-limited-principal</UserId></Principals><Settings><Enabled>false</Enabled></Settings><Actions><Exec><Command>python.exe</Command><Arguments>-I /original/boot.py --role hollywood</Arguments></Exec></Actions></Task>'
        changed=ET.fromstring(definition('windows-task',xml,old,new))
        original=ET.fromstring(xml);original.find('./Actions/Exec/Arguments').text='-I '+new+' --role hollywood'
        self.assertEqual(ET.tostring(changed),ET.tostring(original))

    def test_two_real_operations_cannot_overwrite_at_replace_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'original.pyz';a=root/'a.pyz';b=root/'b.pyz';unit=root/'hollywood-fixture.service'
            for file in (old,a,b):
                with zipfile.ZipFile(file,'w') as z:z.writestr('__main__.py','print(1)')
            unit.write_text('ExecStart=/python '+str(old)+'\n')
            h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            def spec(new):
                return {'schema':'hollywood-api-component/v1','component':unit.name,'kind':'systemd',
                    'definitionFile':str(unit),'definitionSha256':h(unit),
                    'from':{'launcher':str(old),'launcherSha256':h(old)},
                    'to':{'launcher':str(new),'launcherSha256':h(new),'package':str(new),'packageSha256':h(new)}}
            first,second=root/'operation-a',root/'operation-b';prepare(spec(a),first);prepare(spec(b),second)
            code="""import sys
from hollywood_backbone import api_component as api
replace=api.os.replace
def held(source,target):
 print('REPLACE_READY',flush=True)
 assert sys.stdin.buffer.read(1)==b'R'
 return replace(source,target)
api.os.replace=held
api.commit(sys.argv[1])
"""
            child=subprocess.Popen([sys.executable,'-B','-c',code,str(first)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                self.assertTrue(select.select([child.stdout],[],[],5)[0]);self.assertEqual(child.stdout.readline(),b'REPLACE_READY\n')
                other=subprocess.run([sys.executable,'-B','-m','hollywood_backbone.api_component','commit','--operation',str(second)],
                    capture_output=True,text=True,timeout=5)
                self.assertNotEqual(other.returncode,0);self.assertIn('BlockingIOError',other.stderr)
                self.assertFalse((second/'definition-write-intent.json').exists())
                child.stdin.write(b'R');child.stdin.flush();out,err=child.communicate(timeout=5)
                self.assertEqual(child.returncode,0,err.decode());self.assertIn(str(a),unit.read_text())
                with self.assertRaisesRegex(ValueError,'changed outside'):commit(second)
                self.assertIn(str(a),unit.read_text())
            finally:
                if child.poll() is None:child.kill();child.communicate(timeout=5)


if __name__=='__main__':
    os.umask(0o077)
    unittest.main()
