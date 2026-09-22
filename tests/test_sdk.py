import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from nexus_sdk import Context, RobotError, robot
from nexus_sdk.core import encode_object
from nexus_sdk.cli import SDK_VERSION, activation_command, ensure_sdk_requirement


class SDKTests(unittest.TestCase):
    def cli(self, *args, cwd=None):
        env = os.environ.copy()
        source = str(Path(__file__).resolve().parents[1] / 'src')
        env['PYTHONPATH'] = source + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        return subprocess.run([sys.executable, '-m', 'nexus_sdk.cli', *map(str, args)], cwd=cwd,
                              capture_output=True, text=True, env=env)

    def test_scaffold_run_and_inspect(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'my-bot'
            response = self.cli('init', project)
            self.assertEqual(response.returncode, 0, response.stderr)
            result = self.cli('run', '--inputs', 'inputs.json', '--output', 'result.json', cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'message': 'Olá, Minha empresa!', 'processed': 1})
            self.assertEqual(json.loads((project / 'result.json').read_text()), json.loads(result.stdout))
            self.assertIn('Iniciando automação', result.stderr)
            metadata = json.loads(self.cli('--json', 'inspect', cwd=project).stdout)
            self.assertEqual(metadata['entrypoint'], 'bot.py')
            self.assertEqual(len(metadata['checksum']), 64)
            requirements = (project / 'requirements.txt').read_text()
            self.assertIn(f'nexus-sdk @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v{SDK_VERSION}.zip', requirements)
            validation = json.loads(self.cli('--json', 'validate', '--strict', cwd=project).stdout)
            self.assertEqual(validation['warnings'], [])
            self.assertNotEqual(self.cli('init', project).returncode, 0)
            packaged = self.cli('package', cwd=project)
            self.assertEqual(packaged.returncode, 0, packaged.stderr)
            package = json.loads(packaged.stdout)
            self.assertTrue((project / 'dist' / 'my-bot-1.0.0.zip').is_file())
            self.assertEqual(package['version'], '1.0.0')
            self.assertEqual(package['entrypoint'], 'bot.py')
            import zipfile
            with zipfile.ZipFile(project / 'dist' / 'my-bot-1.0.0.zip') as archive:
                self.assertIn('bot.py', archive.namelist())

    def test_package_accepts_version_and_updates_project_config(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'my-bot'
            self.cli('init', project)
            packaged = self.cli('package', '--version', '1.2.3', cwd=project)
            self.assertEqual(packaged.returncode, 0, packaged.stderr)
            package = json.loads(packaged.stdout)
            self.assertEqual(package['version'], '1.2.3')
            self.assertTrue((project / 'dist' / 'my-bot-1.2.3.zip').is_file())
            self.assertIn('version = "1.2.3"', (project / 'nexus.toml').read_text())

    def test_venv_ensures_sdk_requirement_without_removing_bot_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'my-bot'
            self.cli('init', project)
            (project / 'requirements.txt').write_text('playwright==1.62.0\n')
            self.assertTrue(ensure_sdk_requirement(project))
            requirements = (project / 'requirements.txt').read_text()
            self.assertTrue(requirements.startswith(f'nexus-sdk @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v{SDK_VERSION}.zip\n'))
            self.assertIn('playwright==1.62.0', requirements)
            self.assertFalse(ensure_sdk_requirement(project))

    def test_venv_repairs_malformed_sdk_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'requirements.txt').write_text(
                'nexus-sdk @ nexus-sdk @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v0.4.16.zip\n'
                'requests==2.32.0\n',
                encoding='utf-8',
            )
            self.assertTrue(ensure_sdk_requirement(project))
            self.assertEqual(
                (project / 'requirements.txt').read_text(encoding='utf-8'),
                f'nexus-sdk @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v{SDK_VERSION}.zip\n'
                'requests==2.32.0\n',
            )

    def test_activation_command_points_to_project_venv(self):
        command = activation_command(Path('/tmp/bot/.venv'))
        if os.name == 'nt':
            self.assertTrue(command.endswith(r'.venv\Scripts\activate') or command.endswith('.venv/Scripts/activate'))
        else:
            self.assertEqual(command, 'source /tmp/bot/.venv/bin/activate')

    def test_failure_is_nonzero_and_no_success_result(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            self.cli('init', project)
            (project / 'inputs.json').write_text('{"name": ""}')
            response = self.cli('run', '--inputs', 'inputs.json', cwd=project)
            self.assertEqual(response.returncode, 1)
            self.assertIn('ROBOT_ERROR', response.stderr)
            self.assertEqual(response.stdout, '')

    def test_invalid_inputs_and_output_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            self.cli('init', project)
            (project / 'inputs.json').write_text('[]')
            self.assertEqual(self.cli('run', '--inputs', 'inputs.json', cwd=project).returncode, 1)
            original = (project / 'bot.py').read_bytes()
            self.assertEqual(self.cli('run', '--output', 'bot.py', cwd=project).returncode, 1)
            self.assertEqual((project / 'bot.py').read_bytes(), original)

    def test_direct_context(self):
        @robot
        def example(ctx):
            return {'value': ctx.inputs['value'] * 2}
        self.assertEqual(example(Context(inputs={'value': 3})), {'value': 6})
        context = Context(inputs={}, secrets={'erp': {'password': 'hidden'}})
        self.assertEqual(context.secrets['erp']['password'], 'hidden')
        self.assertNotIn('hidden', repr(context))

    def test_contract_validation(self):
        for value in ([], {'value': float('nan')}, {'value': object()}, {'value': 'a' * 32768}):
            with self.assertRaises(RobotError):
                encode_object(value)

    def test_multiple_bots_and_unexpected_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            self.cli('init', project)
            script = project / 'bot.py'
            script.write_text('from nexus_sdk import robot\n@robot\ndef one(ctx): return {}\n@robot\ndef two(ctx): return {}\n')
            result = self.cli('run', cwd=project)
            self.assertEqual(result.returncode, 1)
            self.assertIn('apenas uma', result.stderr)
            script.write_text('from nexus_sdk import robot\n@robot\ndef one(ctx): raise ValueError("secret-value")\n')
            result = self.cli('run', cwd=project)
            self.assertEqual(result.returncode, 1)
            self.assertIn('UNEXPECTED_ERROR', result.stderr)
            self.assertNotIn('secret-value', result.stderr)
            debug = self.cli('run', '--debug', cwd=project)
            self.assertEqual(debug.returncode, 1)
            self.assertIn('Traceback', debug.stderr)
            self.assertIn('secret-value', debug.stderr)

    def test_explicit_main_guard_is_not_run_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            self.cli('init', project)
            (project / 'bot.py').write_text('from nexus_sdk import robot\ncount=0\n@robot\ndef one(ctx):\n global count\n count+=1\n assert count==1\n return {"count":count}\nif __name__=="__main__": one()\n')
            response = self.cli('run', cwd=project)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertEqual(json.loads(response.stdout), {'count': 1})

    def test_project_modules_and_assets_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            self.cli('init', project)
            (project / 'helper.py').write_text('def value(): return "module"\n')
            (project / 'asset.txt').write_text('asset')
            (project / 'bot.py').write_text(
                'from pathlib import Path\nfrom nexus_sdk import robot\nfrom helper import value\n'
                '@robot\ndef run(ctx): return {"module": value(), "asset": Path("asset.txt").read_text()}\n'
            )
            response = self.cli('run', cwd=project)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.assertEqual(json.loads(response.stdout), {'module': 'module', 'asset': 'asset'})
