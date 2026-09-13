import functools
import inspect
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, is_dataclass, asdict
from .errors import RobotError, BusinessError, ConfigurationError, ValidationError, TransientError
from .queues import Queues, MAX_PUBLICATION_BYTES
from .models import Model

LIMIT = 32768


def secret_values(value):
    if isinstance(value, dict):
        return [item for child in value.values() for item in secret_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in secret_values(child)]
    return [value] if isinstance(value, str) and len(value) >= 4 else []


class SecretFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self.values = sorted(secret_values(secrets), key=len, reverse=True)

    def filter(self, record):
        message = record.getMessage()
        for value in self.values:
            message = message.replace(value, '***')
        record.msg, record.args = message, ()
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            for value in self.values:
                record.exc_text = record.exc_text.replace(value, '***')
        return True


class ContextLogger(logging.LoggerAdapter):
    def __init__(self, context):
        super().__init__(logging.getLogger('nexus.robot'), {'execution_id': context.execution_id})
        self.values = sorted(secret_values(context.secrets), key=len, reverse=True)

    def log(self, level, msg, *args, **kwargs):
        message = str(msg) % args if args else str(msg)
        for value in self.values:
            message = message.replace(value, '***')
        super().log(level, message, **kwargs)


def encode_object(value, limit=LIMIT):
    if not isinstance(value, dict):
        raise RobotError('O resultado e os parâmetros devem ser objetos JSON.')
    try:
        content = json.dumps(value, ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise RobotError('Objeto contém um valor não serializável em JSON.') from error
    if len(content) > limit:
        raise ValidationError(f'Objeto JSON excede o limite de {limit // 1024} KB.')
    return content


def read_object(path, limit=LIMIT):
    with Path(path).open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValidationError(f'Arquivo JSON excede o limite de {limit // 1024} KB.')
    try:
        value = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError) as error:
        raise RobotError('Arquivo de parâmetros/resultado não contém JSON válido.') from error
    encode_object(value, limit=limit)
    return value


def write_object(path, value, limit=LIMIT):
    content = encode_object(value, limit=limit)
    path = Path(path)
    # Atomic replace avoids leaving a partially written success result.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Context:
    inputs: dict = field(default_factory=dict)
    execution_id: str = 'local'
    secrets: dict = field(default_factory=dict, repr=False)
    log: logging.LoggerAdapter = field(init=False, repr=False)
    queues: Queues = field(default_factory=Queues, repr=False)
    params: Model | None = field(default=None, init=False, repr=False)
    _write_result: bool = field(default=True, repr=False)

    def __post_init__(self):
        encode_object(self.inputs)
        self.log = ContextLogger(self)

    def require_input(self, name, expected_type=None):
        if name not in self.inputs or self.inputs[name] is None:
            raise ValidationError(f'Parâmetro obrigatório ausente: {name}')
        value = self.inputs[name]
        if expected_type is not None and (not isinstance(value, expected_type)
                                         or expected_type is int and isinstance(value, bool)):
            raise ValidationError(f'Tipo inválido para o parâmetro: {name}')
        return value

    def credential(self, name, field=None):
        if name not in self.secrets:
            raise ConfigurationError(f'Credencial não disponibilizada à automação: {name}')
        value = self.secrets[name]
        if field is not None:
            if not isinstance(value, dict) or field not in value:
                raise ConfigurationError(f'Campo de credencial ausente: {name}.{field}')
            return value[field]
        return value

    def retry(self, operation, *, idempotent=False, attempts=3, delay=0.5):
        if not idempotent:
            raise ConfigurationError('Retries exigem idempotent=True e operação segura para repetição.')
        if type(attempts) is not int or not 1 <= attempts <= 5 or not 0 <= delay <= 10:
            raise ConfigurationError('Use 1 a 5 tentativas e intervalo de 0 a 10 segundos.')
        import random
        for attempt in range(attempts):
            try:
                return operation()
            except TransientError:
                if attempt == attempts - 1:
                    raise
                self.log.warning('Falha temporária; repetindo operação (%s/%s).', attempt + 2, attempts)
                time.sleep(min(10, delay * 2 ** attempt) * random.uniform(0.5, 1))

    @contextmanager
    def step(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise RobotError('Nome da etapa deve conter entre 1 e 120 caracteres.')
        started = time.monotonic()
        self.log.info('Etapa iniciada: %s', name)
        try:
            yield
        except BaseException:
            # Exception text may contain secrets; the runtime handles the error contract.
            self.log.error('Etapa falhou: %s (%.3fs)', name, time.monotonic() - started)
            raise
        else:
            self.log.info('Etapa concluída: %s (%.3fs)', name, time.monotonic() - started)

    @classmethod
    def from_environment(cls):
        source = os.environ.get('NEXUS_INPUT_FILE')
        secrets_file = os.environ.get('NEXUS_SECRETS_FILE')
        queue_file = os.environ.get('NEXUS_QUEUE_CONTEXT_FILE')
        return cls(inputs=read_object(source) if source else {},
                   execution_id=os.environ.get('NEXUS_EXECUTION_ID', 'local'),
                   secrets=read_object(secrets_file, limit=256 * 1024) if secrets_file else {},
                   queues=Queues(read_object(queue_file) if queue_file else {}))


class Robot:
    def __init__(self, function):
        if inspect.iscoroutinefunction(function):
            raise TypeError('Esta versão do SDK suporta funções síncronas.')
        functools.update_wrapper(self, function)
        self.function = function
        self.executed = False
        self.result = None

    def __call__(self, ctx=None):
        self.executed, self.result = False, None
        context = ctx if ctx is not None else Context.from_environment()
        result = self.function(context)
        encode_object(result)
        publications = context.queues.publications
        if publications and context._write_result:
            queue_target = os.environ.get('NEXUS_QUEUE_OUTPUT_FILE')
            if not queue_target:
                raise ConfigurationError('Runtime sem suporte à publicação em filas; atualize o agent.')
            write_object(queue_target, {'publications': publications}, limit=MAX_PUBLICATION_BYTES + 1024)
        target = os.environ.get('NEXUS_RESULT_FILE') if context._write_result else None
        if target:
            write_object(target, result)
        self.result = result
        self.executed = True
        return result


class Automation:
    """Synchronous lifecycle: setup -> run -> teardown, including failure cleanup."""
    input_model = None
    output_model = None
    required_credentials = ()

    def setup(self, ctx):
        pass

    def run(self, ctx):
        raise NotImplementedError('Implemente run(ctx) na automação.')

    def teardown(self, ctx):
        pass

    def _execute(self, ctx):
        try:
            with ctx.step('Preparação'):
                self.setup(ctx)
            result = self.run(ctx)
            if self.output_model:
                result = self.output_model.parse(result, path='result').to_dict()
            elif is_dataclass(result):
                result = asdict(result)
            encode_object(result)
        except BaseException:
            try:
                with ctx.step('Finalização'):
                    self.teardown(ctx)
            except BaseException:
                ctx.log.error('Falha adicional na finalização; preservando o erro original.')
            raise
        else:
            with ctx.step('Finalização'):
                self.teardown(ctx)
            return result


def robot(function):
    """Decorate a synchronous function or an Automation class; classes are fresh per run."""
    if inspect.isclass(function):
        if not issubclass(function, Automation):
            raise TypeError('Classes @robot devem herdar de Automation.')
        if any(inspect.iscoroutinefunction(getattr(function, method)) for method in ('setup', 'run', 'teardown')):
            raise TypeError('Os métodos da automação devem ser síncronos.')

        @functools.wraps(function, updated=())
        def execute(context):
            if function.input_model:
                context.params = function.input_model.parse(context.inputs)
            for name in function.required_credentials:
                context.credential(name)
            return function()._execute(context)

        return Robot(execute)
    return Robot(function)
