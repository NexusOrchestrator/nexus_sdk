from dataclasses import dataclass
import json
import logging
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nexus_sdk import Automation, Model, Context, BusinessError, ConfigurationError, ValidationError, TransientError, robot
from nexus_sdk.queues import Queues
from nexus_sdk.testing import run_robot
from nexus_sdk.project import validate_project
import test_sdk


@dataclass
class Item(Model):
    id: int
    labels: list[str]


@dataclass
class Entrada(Model):
    item: Item
    optional: str | None = None


@dataclass
class Saida(Model):
    total: int


class MVPTests(unittest.TestCase):
    def test_contracts_validate_before_setup_and_before_success(self):
        calls = []
        @robot
        class Bot(Automation):
            input_model = Entrada
            output_model = Saida
            def setup(self, ctx):
                calls.append('setup')
            def run(self, ctx):
                return Saida(total=ctx.params.item.id)
            def teardown(self, ctx):
                calls.append('teardown')
        with self.assertRaises(ValidationError):
            run_robot(Bot, inputs={'item': {'id': True, 'labels': []}})
        self.assertEqual(calls, [])
        result = run_robot(Bot, inputs={'item': {'id': 3, 'labels': ['ok']}})
        self.assertEqual(result.result, {'total': 3})
        self.assertEqual(calls, ['setup', 'teardown'])
        with self.assertRaises(ValidationError):
            Entrada.parse({'item': {'id': 3, 'labels': []}, 'unknown': 1})

    def test_credentials_and_local_logs_are_safe(self):
        @robot
        class Bot(Automation):
            required_credentials = ('erp',)
            def run(self, ctx):
                ctx.log.info('Senha: %s', ctx.credential('erp', 'password'))
                return {}
        with self.assertRaises(ConfigurationError):
            run_robot(Bot)
        with self.assertLogs('nexus.robot') as logs:
            run_robot(Bot, credentials={'erp': {'password': 'do-not-display'}})
        self.assertNotIn('do-not-display', '\n'.join(logs.output))
        self.assertIn('***', '\n'.join(logs.output))

    def test_exception_log_filter_masks_traceback(self):
        from nexus_sdk.core import SecretFilter
        try:
            raise ValueError('password-sensitive-value')
        except ValueError:
            record = logging.LogRecord('test', logging.ERROR, '', 1, 'failed', (), sys.exc_info())
        SecretFilter({'password': 'password-sensitive-value'}).filter(record)
        formatted = logging.Formatter().format(record)
        self.assertNotIn('password-sensitive-value', formatted)
        self.assertIn('***', formatted)

    def test_queue_consumption_and_idempotent_staging(self):
        @robot
        def bot(ctx):
            message = ctx.queues.consume('entrada')
            self.assertEqual(message.parse(Item).id, 7)
            self.assertIsNone(ctx.queues.consume())
            ctx.queues.publish({'id': 7}, key='item:7', queue='saida')
            ctx.queues.publish({'id': 7}, key='item:7')
            return {'ok': True}
        context = {'input': {'id': 'in', 'name': 'entrada'}, 'output': {'id': 'out', 'name': 'saida'},
                   'message': {'id': 'message', 'payload': {'id': 7, 'labels': []}}}
        response = run_robot(bot, queues=context)
        self.assertEqual(len(response.publications), 1)
        queues = Queues(context)
        with self.assertRaises(ConfigurationError):
            queues.publish({}, key='a', queue='foreign')
        queues.publish({}, key='same')
        with self.assertRaises(ValidationError):
            queues.publish({'different': True}, key='same')
        with self.assertRaises(ValidationError):
            queues.publish({'large': 'x' * 30720}, key='big')

    def test_retries_require_opt_in_and_only_repeat_transient_failures(self):
        ctx = Context()
        with self.assertRaises(ConfigurationError):
            ctx.retry(lambda: {})
        calls = []
        def operation():
            calls.append(1)
            if len(calls) < 3:
                raise TransientError('temporary')
            return 42
        with patch('nexus_sdk.core.time.sleep'):
            self.assertEqual(ctx.retry(operation, idempotent=True), 42)
        self.assertEqual(len(calls), 3)
        def business():
            raise BusinessError('no retry')
        with patch('nexus_sdk.core.time.sleep') as sleep:
            with self.assertRaises(BusinessError):
                ctx.retry(business, idempotent=True)
            sleep.assert_not_called()

    def test_validate_is_static_checks_compatibility_and_scaffold_tests_run(self):
        cli = test_sdk.SDKTests().cli
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            cli('init', project)
            validated = cli('validate', '--strict', cwd=project)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            import subprocess, sys
            tested = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                                    cwd=project, capture_output=True, text=True)
            self.assertEqual(tested.returncode, 0, tested.stderr)
            script = project / 'bot.py'
            script.write_text('raise RuntimeError("must not execute")\n' + script.read_text())
            self.assertEqual(cli('validate', cwd=project).returncode, 0)
            config = project / 'nexus.toml'
            config.write_text(config.read_text().replace('sdk_min = "0.4.0"', 'sdk_min = "99.0.0"'))
            self.assertNotEqual(cli('validate', cwd=project).returncode, 0)
            # Packaging can target a newer environment without executing source code.
            self.assertEqual(cli('package', cwd=project).returncode, 0)

    def test_missing_credentials_and_queues_fail_preflight(self):
        cli = test_sdk.SDKTests().cli
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'bot'
            cli('init', project)
            config = project / 'nexus.toml'
            config.write_text(config.read_text().replace('credentials = []', 'credentials = ["erp"]') + '\n[queues]\noutput = "saida"\n')
            with self.assertRaises(ConfigurationError):
                validate_project(project, credentials={}, queues={})
            with self.assertRaises(ConfigurationError):
                validate_project(project, credentials={'erp': {}}, queues={})
            validate_project(project, credentials={'erp': {}}, queues={'output': {'name': 'saida'}})
