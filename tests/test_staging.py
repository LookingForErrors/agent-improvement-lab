"""Portable boundary covers promised checks and preserves unrelated/private inputs."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import yaml
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stage_boundary',ROOT/'scripts/stage_offline.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
spec=importlib.util.spec_from_file_location('wheel_boundary',ROOT/'scripts/verify_installed.py')
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)

class StagingTests(unittest.TestCase):
    def test_public_package_contains_relative_imports_and_archived_engine(self):
        names=set(stage.CORE)
        for name in names:
            tree=ast.parse((ROOT/'agent_improvement_lab'/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and node.level==1:
                    modules=[node.module.split('.')[0]] if node.module else [part.name for part in node.names]
                    for module in modules:self.assertIn(module+'.py',names, f'{name} imports missing {module}')
                if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SOURCES' for t in node.targets):
                    for source in ast.literal_eval(node.value):self.assertIn(source,names,f'{name} archives missing {source}')
    def test_ci_promised_tests_and_wheel_helpers_exist_in_snapshot(self):
        text=(ROOT/'.github/workflows/offline.yml').read_text();yaml.safe_load(text)
        names=set(stage.inventory())
        for pattern in re.findall(r"test_[a-z_]+\.py",text):
            self.assertTrue('tests/'+pattern in names or (pattern in ['test_public_demo.py','test_catalog.py','test_saved_experiment.py','test_family_site.py','test_curated_replay.py'] and 'scripts/stage_release.py' in text))
        for name in ['tests/test_recovery_guard.py','tests/test_staging.py','scripts/verify_installed.py','requirements/combined.lock.txt','examples/routing-experiment/build_recovery_demo.py','.github/workflows/offline.yml']:self.assertIn(name,names)
    def test_snapshot_hashes_match_and_no_private_tree_is_copied(self):
        with tempfile.TemporaryDirectory() as root:
            out=Path(root)/'source';files=stage.stage(out)
            self.assertEqual(set(files),set(stage.inventory())|{'README.md'})
            for name,digest in files.items():self.assertEqual(hashlib.sha256((out/name).read_bytes()).hexdigest(),digest)
            for name in stage.inventory():self.assertEqual((ROOT/name).read_bytes(),(out/name).read_bytes())
            self.assertFalse(any(part in ['artifacts','annotation_data','datasets','tasks','logs','decisions','.venv','.venv-graph'] for name in files for part in Path(name).parts))
            self.assertTrue((out/'tests/test_recovery_guard.py').exists())
    def test_existing_directory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            out=Path(root);p=out/'human-data.txt';p.write_text('keep')
            with self.assertRaises(ValueError):stage.stage(out)
            self.assertEqual(p.read_text(),'keep')
    def test_symlink_input_is_rejected_before_any_output(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);private=root/'private.txt';private.write_text('keep');source=root/'input';source.mkdir();(source/'public.txt').symlink_to(private);out=root/'output'
            with patch.object(stage,'ROOT',source),patch.object(stage,'inventory',return_value=['public.txt']):
                with self.assertRaises(ValueError):stage.stage(out)
            self.assertFalse(out.exists());self.assertEqual(private.read_text(),'keep')
    def test_symlink_parent_is_rejected_before_any_output(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);private=root/'private';private.mkdir();(private/'file.txt').write_text('keep');source=root/'input';source.mkdir();(source/'public').symlink_to(private,target_is_directory=True);out=root/'output'
            with patch.object(stage,'ROOT',source),patch.object(stage,'inventory',return_value=['public/file.txt']):
                with self.assertRaises(ValueError):stage.stage(out)
            self.assertFalse(out.exists());self.assertEqual((private/'file.txt').read_text(),'keep')
    def test_combined_pins_are_exact_union_without_new_versions(self):
        def pins(path):
            result={}
            for line in path.read_text().splitlines():
                if line and not line.startswith('#'):
                    name,version=line.split('==');name=re.sub(r'[-_.]+','-',name.lower());self.assertNotIn(name,result);result[name]=version
            return result
        left=pins(ROOT/'requirements/execution.lock.txt');right=pins(ROOT/'requirements/workflow.lock.txt')
        for name in set(left)&set(right):self.assertEqual(left[name],right[name])
        self.assertEqual(pins(ROOT/'requirements/combined.lock.txt'),dict(left,**right))
    def wheel_fixture(self,root,mutation=None):
        root=Path(root);snapshot=root/'source';stage.stage(snapshot);directory=root/'wheel';directory.mkdir()
        manifest=json.loads((snapshot/'STAGING-MANIFEST.json').read_text())['files']
        with zipfile.ZipFile(directory/'contract.whl','w') as bundle:
            for name in manifest:
                if name.split('/')[0] in ['agent_improvement_lab','annotation_ui']:
                    data=(snapshot/name).read_bytes()
                    bundle.writestr(name,data+b'\n# changed\n' if mutation=='tamper' and name=='agent_improvement_lab/schemas.py' else data)
            for name in ['METADATA','WHEEL','entry_points.txt','top_level.txt','RECORD']:
                bundle.writestr('agent_improvement_lab-0.1.0.dist-info/'+name,'fixture metadata, not an installable wheel')
            if 'LICENSE' in manifest:
                bundle.writestr('agent_improvement_lab-0.1.0.dist-info/licenses/LICENSE',(snapshot/'LICENSE').read_bytes())
            if mutation=='private':bundle.writestr('annotation_data/private.sqlite3',b'private fixture')
            if mutation=='duplicate':
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    bundle.writestr('agent_improvement_lab/schemas.py',(snapshot/'agent_improvement_lab/schemas.py').read_bytes())
        return snapshot,directory
    def test_wheel_boundary_accepts_exact_package_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot,directory=self.wheel_fixture(root)
            result=verify.wheel_inventory(snapshot,directory)
            self.assertEqual(result['package_file_count'],24)
    def test_wheel_boundary_rejects_private_tampered_or_duplicate_members(self):
        for mutation in ['private','tamper','duplicate']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as root:
                snapshot,directory=self.wheel_fixture(root,mutation)
                with self.assertRaises(ValueError):verify.wheel_inventory(snapshot,directory)

if __name__=='__main__':unittest.main()
