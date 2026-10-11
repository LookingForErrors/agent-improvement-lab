"""Metadata/resource boundaries, without installing packages or building a wheel."""
import importlib
from importlib.resources import files
from pathlib import Path
import os
import tempfile
import tomllib
import unittest
import annotation_ui

ROOT=Path(__file__).resolve().parents[1]


class PackagingChecks(unittest.TestCase):
    def test_public_entrypoints_resolve_without_live_provider(self):
        document=tomllib.loads((ROOT/'pyproject.toml').read_text())
        self.assertEqual(document['build-system']['requires'],['setuptools==84.0.0'])
        for name,target in document['project']['scripts'].items():
            module,function=target.split(':')
            self.assertNotIn('live',module)
            self.assertTrue(callable(getattr(importlib.import_module(module),function)),name)
    def test_assets_are_package_resources_outside_checkout_workdir(self):
        document=tomllib.loads((ROOT/'pyproject.toml').read_text())
        expected=document['tool']['setuptools']['package-data']['annotation_ui']
        with tempfile.TemporaryDirectory() as target:
            previous=Path.cwd()
            try:
                os.chdir(target)
                for name in expected:
                    actual=files(annotation_ui).joinpath(name).read_bytes()
                    self.assertGreater(len(actual),100)
                    self.assertEqual(actual,(ROOT/'annotation_ui'/name).read_bytes())
            finally:os.chdir(previous)
    def test_ui_path_traversal_and_private_paths_are_not_static_resources(self):
        from agent_improvement_lab.annotation import handler_for
        import threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for('/does/not/exist',0))
        # Handler host must be bound to the actual assigned port.
        server.RequestHandlerClass=handler_for('/does/not/exist',server.server_port)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            for path in ['/','/app.js','/style.css']:
                with urllib.request.urlopen(base+path) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
            for path in ['/../pyproject.toml','/rubric.txt','/annotations.sqlite3','/private.json']:
                with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+path)
                self.assertEqual(caught.exception.code,404)
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
