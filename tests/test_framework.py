import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
from nexus_sdk import Automation, Context, RobotError, robot
import test_sdk


class FrameworkTests(unittest.TestCase):
    def test_lifecycle_fresh_instance_and_cleanup(self):
        events = []
        @robot
        class Bot(Automation):
            def setup(self, ctx):
                self.count = 0
                events.append('setup')
            def run(self, ctx):
                self.count += 1
                events.append('run')
                return {'count': self.count}
            def teardown(self, ctx):
                events.append('teardown')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Bot(Context()), {'count': 1})
            self.assertEqual(Bot(Context()), {'count': 1})
        self.assertEqual(events, ['setup', 'run', 'teardown'] * 2)

    def test_original_error_survives_cleanup_failure_and_no_result_written(self):
        events = []
        @robot
        class Bot(Automation):
            def setup(self, ctx):
                raise RobotError('original')
            def run(self, ctx):
                events.append('unexpected')
                return {}
            def teardown(self, ctx):
                events.append('cleanup')
                raise RuntimeError('secret-must-not-be-logged')
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.json'
            with patch.dict(os.environ, {'NEXUS_RESULT_FILE': str(result)}), self.assertLogs('nexus.robot') as logs:
                with self.assertRaisesRegex(RobotError, 'original'):
                    Bot(Context())
            self.assertFalse(result.exists())
            self.assertNotIn('secret-must-not-be-logged', '\n'.join(logs.output))
        self.assertEqual(events, ['cleanup'])

    def test_context_validation_and_step_error_preserved(self):
        ctx = Context(inputs={'count': True}, secrets={'erp': {'password': 'hidden'}})
        self.assertEqual(ctx.credential('erp'), {'password': 'hidden'})
        with self.assertRaises(RobotError):
            ctx.require_input('missing')
        with self.assertRaises(RobotError):
            ctx.require_input('count', int)
        with self.assertRaises(RobotError):
            ctx.credential('missing')
        with self.assertLogs('nexus.robot') as logs:
            with self.assertRaisesRegex(ValueError, 'hidden'):
                with ctx.step('Processar'):
                    raise ValueError('hidden')
        self.assertNotIn('hidden', '\n'.join(logs.output))
        self.assertIn('Etapa falhou', '\n'.join(logs.output))

    def test_packaging_uses_project_name_outside_project_and_is_reproducible(self):
        cli = test_sdk.SDKTests().cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'meu-projeto'
            self.assertEqual(cli('init', project).returncode, 0)
            first = cli('package', '--project', project, cwd=root)
            self.assertEqual(first.returncode, 0, first.stderr)
            metadata = json.loads(first.stdout)
            self.assertEqual(metadata['file'], str((project / 'dist' / 'meu-projeto.zip').resolve()))
            second = cli('package', '--project', project, cwd=root)
            self.assertEqual(json.loads(second.stdout)['checksum'], metadata['checksum'])
            config = project / 'nexus.toml'
            config.write_text('entrypoint = "bot.py"\nversion = "1.0.0"\n')
            self.assertEqual(json.loads(cli('package', '--project', project, cwd=root).stdout)['file'], metadata['file'])
            config.write_text('name = "financeiro"\nentrypoint = "bot.py"\n')
            self.assertTrue(json.loads(cli('package', '--project', project, cwd=root).stdout)['file'].endswith('/financeiro.zip'))
            explicit = root / 'custom.zip'
            self.assertEqual(json.loads(cli('package', '--project', project, '--output', explicit).stdout)['file'], str(explicit.resolve()))
            config.write_text('name = "../escape"\nentrypoint = "bot.py"\n')
            self.assertEqual(cli('package', '--project', project).returncode, 1)
            self.assertFalse((project / 'escape.zip').exists())


if __name__ == '__main__':
    unittest.main()
