import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from .core import RobotError, read_object, write_object
from .project import validate_project
from .credentials import save_credentials, load_credentials, clear_credentials, set_default_environment, list_profiles, use_profile
from .http import api_request, api_upload, api_download

SDK_VERSION = '0.4.17'
SDK_RUNTIME_MIN = '0.4.0'
SDK_REQUIREMENT = f'nexus-sdk @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v{SDK_VERSION}.zip'
SDK_GITIGNORE = '.venv/\n__pycache__/\nresult.json\n.env\n*.local.json\nfixtures.json\ndist/\n'
VERSION_PATTERN = re.compile(r'\d+\.\d+(?:\.\d+)?')

BOT = '''from dataclasses import dataclass
from nexus_sdk import Automation, Context, Model, RobotError, robot


@dataclass
class Entrada(Model):
    name: str


@dataclass
class Saida(Model):
    message: str
    processed: int


@robot
class MeuBot(Automation):
    input_model = Entrada
    output_model = Saida

    def run(self, ctx: Context):
        with ctx.step("Processar entrada"):
            nome = ctx.params.name
            if not nome.strip():
                raise RobotError("Informe um nome não vazio no parâmetro name.")
            ctx.log.info("Iniciando automação")
            return {"message": f"Olá, {nome}!", "processed": 1}
'''



def _format_cell(value):
    if value is None:
        return '-'
    if isinstance(value, bool):
        return 'sim' if value else 'não'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_table(items):
    if not items:
        print('(nenhum resultado)')
        return
    columns = list(dict.fromkeys(key for item in items for key in item.keys()))
    widths = {column: max(len(column), *(len(_format_cell(item.get(column))) for item in items)) for column in columns}
    print('  '.join(column.upper().ljust(widths[column]) for column in columns))
    print('  '.join('-' * widths[column] for column in columns))
    for item in items:
        print('  '.join(_format_cell(item.get(column)).ljust(widths[column]) for column in columns))


def print_result(result, as_json=False):
    """Pretty-print API responses; pass --json to get the raw payload instead."""
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif isinstance(result, list):
        print_table(result)
    elif isinstance(result, dict) and isinstance(result.get('items'), list):
        # Paginated responses ({items, total, limit, offset}); render items as a table and metadata below.
        print_table(result['items'])
        meta = {key: value for key, value in result.items() if key != 'items'}
        if meta:
            print()
            for key, value in meta.items():
                print(f'{key}: {_format_cell(value)}')
    elif isinstance(result, dict):
        for key, value in result.items():
            print(f'{key}: {_format_cell(value)}')
    else:
        print(result)


def init_project(path):
    # Never merge a scaffold into an existing directory or overwrite user code.
    path.mkdir(parents=True, exist_ok=False)
    (path / 'bot.py').write_text(BOT, encoding='utf-8')
    (path / 'tests').mkdir()
    (path / 'tests' / 'test_bot.py').write_text(
        'import unittest\nfrom bot import MeuBot\nfrom nexus_sdk.testing import run_robot\n\n'
        'class BotTests(unittest.TestCase):\n'
        '    def test_processa_entrada(self):\n'
        '        response = run_robot(MeuBot, inputs={"name": "Teste"})\n'
        '        self.assertEqual(response.result["processed"], 1)\n', encoding='utf-8')
    (path / 'nexus.toml').write_text('name = ' + json.dumps(path.resolve().name, ensure_ascii=False) + f'\nentrypoint = "bot.py"\nversion = "1.0.0"\ncredentials = []\n\n[runtime]\npython = "3.12"\nsdk_min = "{SDK_RUNTIME_MIN}"\n', encoding='utf-8')
    (path / 'inputs.json').write_text('{"name": "Minha empresa"}\n', encoding='utf-8')
    (path / 'fixtures.json').write_text(
        json.dumps({'inputs': {'name': 'Minha empresa'}, 'environment': {}, 'credentials': {}, 'queues': {}}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (path / 'requirements.txt').write_text(f'{SDK_REQUIREMENT}\n# Adicione abaixo apenas as bibliotecas usadas pelo seu bot.\n', encoding='utf-8')
    (path / '.gitignore').write_text(SDK_GITIGNORE, encoding='utf-8')
    (path / 'README.md').write_text(f'# Framework Nexus\n\nExecute localmente:\n\n```sh\nnexus run --inputs inputs.json\n```\n\nCom credenciais/filas de teste:\n\n```sh\nnexus run --fixtures fixtures.json\n```\n\nEmpacote para publicar no Nexus:\n\n```sh\nnexus package\n```\n\nO `requirements.txt` já fixa o SDK na tag pública `{SDK_VERSION}`:\n\n```txt\n{SDK_REQUIREMENT}\n```\n\nAdicione suas dependências de automação abaixo dessa linha.\n', encoding='utf-8')
    print(f'Projeto criado em {path.resolve()}\nEntre na pasta e execute: nexus run --inputs inputs.json')


def entrypoint(path):
    root = path.resolve()
    with (root / 'nexus.toml').open('rb') as stream:
        config = tomllib.load(stream)
    value = config.get('entrypoint', 'bot.py')
    if not isinstance(value, str):
        raise RobotError('entrypoint deve ser um caminho de arquivo.')
    script = (root / value).resolve()
    if not script.is_relative_to(root) or script.suffix != '.py' or not script.is_file():
        raise RobotError('entrypoint deve apontar para um arquivo Python dentro do projeto.')
    return script


def load_project_config(root: Path):
    with (root / 'nexus.toml').open('rb') as stream:
        return tomllib.load(stream)


def validate_version(value: str):
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise RobotError('Versão deve usar major.minor ou major.minor.patch, por exemplo 1.2.0.')
    return value


def update_project_version(root: Path, version: str):
    config_path = root / 'nexus.toml'
    content = config_path.read_text(encoding='utf-8')
    if re.search(r'(?m)^version\s*=', content):
        content = re.sub(r'(?m)^version\s*=.*$', f'version = {json.dumps(version)}', content, count=1)
    else:
        content = f'version = {json.dumps(version)}\n' + content
    config_path.write_text(content, encoding='utf-8')


def safe_zip_name(name: str):
    if (not isinstance(name, str) or not name or name.startswith('.') or name.endswith(('.', ' '))
            or any(character in name for character in '/\\:*?"<>|')
            or any(ord(character) < 32 for character in name)):
        raise RobotError('Nome do projeto inválido para um arquivo ZIP.')
    return name


def ensure_sdk_requirement(root: Path):
    requirements = root / 'requirements.txt'
    current = requirements.read_text(encoding='utf-8') if requirements.exists() else ''
    if SDK_REQUIREMENT in current:
        return False
    lines = current.splitlines()
    without_old_sdk = [
        line for line in lines
        if not re.match(r'^\s*nexus[-_]sdk\b', line, re.IGNORECASE)
        and 'github.com/NexusOrchestrator/nexus_sdk' not in line
        and 'github.com/nexusorchestrator/nexus_sdk' not in line.lower()
    ]
    content = '\n'.join([SDK_REQUIREMENT, *without_old_sdk]).rstrip() + '\n'
    requirements.write_text(content, encoding='utf-8')
    return True


def activation_command(venv_dir: Path):
    if os.name == 'nt':
        return str(venv_dir / 'Scripts' / 'activate')
    return f'source {venv_dir / "bin" / "activate"}'


def open_venv_shell(root: Path, venv_dir: Path):
    environment = os.environ.copy()
    bin_dir = venv_dir / ('Scripts' if os.name == 'nt' else 'bin')
    environment['VIRTUAL_ENV'] = str(venv_dir)
    environment['PATH'] = str(bin_dir) + os.pathsep + environment.get('PATH', '')
    environment.pop('PYTHONHOME', None)
    shell = os.environ.get('COMSPEC') if os.name == 'nt' else os.environ.get('SHELL', '/bin/sh')
    print('Ambiente ativado nesta sessão. Digite exit para sair.')
    subprocess.run([shell], cwd=root, env=environment, check=False)


def create_virtualenv(args):
    root = args.project.resolve()
    validate_project(root, check_environment=False)
    ensure_sdk_requirement(root)
    venv_dir = args.path.resolve() if args.path else root / '.venv'
    if venv_dir.exists() and not args.recreate:
        raise RobotError(f'Ambiente virtual já existe em {venv_dir}. Use --recreate para refazer.')
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    print(f'Criando ambiente virtual em {venv_dir}')
    subprocess.run([sys.executable, '-m', 'venv', str(venv_dir)], cwd=root, check=True)
    python = venv_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], cwd=root, check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', SDK_REQUIREMENT], cwd=root, check=True)
    if args.install_requirements:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], cwd=root, check=True)
    activate = activation_command(venv_dir)
    print(json.dumps({'venv': str(venv_dir), 'python': str(python), 'activate': activate}, ensure_ascii=False, indent=2))
    print(f'\nPara ativar no seu terminal:\n  {activate}')
    if args.shell:
        open_venv_shell(root, venv_dir)


def package_project(args):
    root = args.project.resolve()
    if args.version:
        update_project_version(root, validate_version(args.version))
    validate_project(root, check_environment=False)
    script = entrypoint(args.project)
    config = load_project_config(root)
    name = safe_zip_name(config.get('name', root.name))
    version = validate_version(config.get('version', '1.0.0'))
    output = (args.output or root / 'dist' / f'{name}-{version}.zip').resolve()
    if output.suffix.lower() != '.zip':
        raise RobotError('O pacote deve ter extensão .zip.')
    if output.is_relative_to(root):
        ignored_output = output
    else:
        ignored_output = None
    output.parent.mkdir(parents=True, exist_ok=True)
    ignored = {'.git', '.venv', '__pycache__', '.pytest_cache', 'dist', '.env'}
    files = [path for path in root.rglob('*') if path.is_file()
             and not any(part in ignored for part in path.relative_to(root).parts)
             and path != ignored_output
             and not path.name.startswith('.env.') and not path.name.endswith('.local.json') and path.name != 'result.json']
    if any(path.is_symlink() or not path.resolve().is_relative_to(root) for path in files):
        raise RobotError('O pacote não pode conter links simbólicos.')
    if sum(path.stat().st_size for path in files) > 250 * 1024 * 1024:
        raise RobotError('O projeto excede 250 MB descompactados.')
    if len(files) > 2000:
        raise RobotError('O projeto contém mais de 2000 arquivos.')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    if output.stat().st_size > 50 * 1024 * 1024:
        output.unlink()
        raise RobotError('O pacote excede 50 MB.')
    print(json.dumps({'file': str(output), 'version': version, 'entrypoint': script.relative_to(root).as_posix(),
                      'checksum': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'size_bytes': output.stat().st_size}, indent=2))


def _default_playwright_browsers_path():
    """Where `playwright install` puts browsers on the real HOME, so isolated runs can still find them."""
    if 'PLAYWRIGHT_BROWSERS_PATH' in os.environ:
        return os.environ['PLAYWRIGHT_BROWSERS_PATH']
    if sys.platform == 'darwin':
        return str(Path.home() / 'Library' / 'Caches' / 'ms-playwright')
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))
        return str(Path(base) / 'ms-playwright')
    return str(Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'ms-playwright')


def _parse_dotenv(path):
    """Minimal KEY=VALUE parser: no interpolation, no multiline values, '#' starts a comment."""
    variables = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip().removeprefix('export ').strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            variables[key] = value
    return variables


def _flatten_fixture_queues(queues):
    """fixtures.json nests message under input for readability; the runtime queue context keeps it flat."""
    queues = dict(queues)
    source = queues.get('input')
    if isinstance(source, dict) and 'message' in source:
        source = dict(source)
        queues['message'] = source.pop('message')
        queues['input'] = source
    return queues


def run_local(args):
    script = entrypoint(args.project)
    fixtures = read_object(args.fixtures, limit=512 * 1024) if args.fixtures else {}
    for field in ('inputs', 'credentials', 'queues', 'environment'):
        if not isinstance(fixtures.get(field, {}), dict):
            raise RobotError(f'O campo {field} do fixture deve ser um objeto JSON.')
    inputs = read_object(args.inputs) if args.inputs else fixtures.get('inputs', {})
    validate_project(args.project, credentials=fixtures.get('credentials', {}), queues=fixtures.get('queues', {}))
    if args.output and args.output.resolve() in {script, args.inputs.resolve() if args.inputs else script, (args.project / 'nexus.toml').resolve()}:
        raise RobotError('O resultado não pode sobrescrever o código, parâmetros ou configuração.')
    protected = {script, (args.project / 'nexus.toml').resolve()}
    protected.update(value.resolve() for value in (args.inputs, args.fixtures) if value)
    for output in (args.output, args.publications_output):
        if output and (output.resolve() in protected or output.suffix.lower() != '.json'):
            raise RobotError('Use um arquivo JSON de saída diferente dos arquivos de entrada/configuração.')
    if args.output and args.publications_output and args.output.resolve() == args.publications_output.resolve():
        raise RobotError('Resultado e publicações precisam de arquivos distintos.')
    dotenv_path = args.project / '.env'
    bot_environment = _parse_dotenv(dotenv_path) if dotenv_path.is_file() else {}
    bot_environment.update({key: str(value) for key, value in fixtures.get('environment', {}).items()})
    with tempfile.TemporaryDirectory(prefix='nexus-local-') as directory:
        work = Path(directory)
        source = work / 'inputs.json'
        result_file = work / 'result.json'
        secrets_file = work / 'secrets.json'
        queue_file = work / 'queues.json'
        write_object(source, inputs)
        write_object(secrets_file, fixtures.get('credentials', {}), limit=256 * 1024)
        write_object(queue_file, _flatten_fixture_queues(fixtures.get('queues', {})))
        environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'LANG') if key in os.environ}
        environment.update(bot_environment)
        environment.update(HOME=str(work), TMPDIR=str(work), TEMP=str(work), TMP=str(work),
                           PLAYWRIGHT_BROWSERS_PATH=_default_playwright_browsers_path(),
                           NEXUS_INPUT_FILE=str(source), NEXUS_RESULT_FILE=str(result_file), NEXUS_EXECUTION_ID='local',
                           NEXUS_SECRETS_FILE=str(secrets_file), NEXUS_QUEUE_CONTEXT_FILE=str(queue_file),
                           NEXUS_QUEUE_OUTPUT_FILE=str(work / 'publications.json'))
        command = [sys.executable, '-I', '-m', 'nexus_sdk.runtime', '--script', str(script),
                   '--project-root', str(args.project.resolve())]
        if args.debug:
            command.append('--debug')
        result = subprocess.run(command,
                                cwd=args.project.resolve(), env=environment)
        if result.returncode:
            return result.returncode
        output = read_object(result_file)
        if args.output:
            write_object(args.output, output)
        if args.publications_output:
            publications = work / 'publications.json'
            value = read_object(publications, limit=257 * 1024) if publications.exists() else {'publications': []}
            write_object(args.publications_output, value, limit=257 * 1024)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0


def perform_login(base_url, token, profile=None):
    base_url = base_url.rstrip('/')
    if not token.startswith('nxs_'):
        raise RobotError('Token inválido. Gere um token pessoal em Perfil > Tokens de API.')
    account = api_request(base_url, 'GET', '/auth/profile', token=token)
    tokens = api_request(base_url, 'GET', '/auth/tokens', token=token)
    organization_id, organization_name = None, None
    matching = [item for item in (tokens or []) if item.get('prefix') and token.startswith(item['prefix'])]
    if matching:
        organization_id = matching[0]['organization_id']
        organization_name = matching[0]['organization_name']
    profile_name = save_credentials(base_url, token, organization_id, organization_name, profile=profile)
    return account, load_credentials(), profile_name


def login(args):
    token = args.token or input('Cole seu Personal Access Token (nxs_...): ').strip()
    account, credentials, profile_name = perform_login(args.api_url, token, profile=args.profile)
    print_result({'logged_in_as': account.get('email'), 'api_url': credentials['api_base_url'],
                  'organization': credentials.get('organization_name'), 'profile': profile_name}, args.json)


def logout(args):
    clear_credentials(profile=args.profile)
    print(f'Perfil "{args.profile}" removido.' if args.profile else 'Todas as sessões locais foram removidas.')


def whoami(args):
    credentials = require_credentials()
    active, _ = list_profiles()
    account = api_request(credentials['api_base_url'], 'GET', '/auth/profile', token=credentials['token'])
    print_result({'email': account.get('email'), 'api_url': credentials['api_base_url'],
                  'organization': credentials.get('organization_name'), 'profile': active}, args.json)


def profile_list(args):
    active, profiles = list_profiles()
    print_result([{'profile': name, 'api_url': data['api_base_url'], 'organization': data.get('organization_name'),
                    'active': name == active} for name, data in profiles.items()], args.json)


def profile_use(args):
    try:
        use_profile(args.name)
    except KeyError:
        raise RobotError(f'Perfil "{args.name}" não encontrado. Rode: nexus profile-list')
    print_result({'active_profile': args.name}, args.json)


def require_credentials():
    """Every action that touches the API goes through here: prompts an inline login instead of just failing."""
    credentials = load_credentials()
    if credentials:
        return credentials
    print('Você não está autenticado. Faça login para continuar.')
    base_url = os.environ.get('NEXUS_API_URL', 'https://api.nexusorchestrator.com')
    entered_url = input(f'URL da API [{base_url}]: ').strip()
    token = input('Cole seu Personal Access Token (nxs_...): ').strip()
    perform_login(entered_url or base_url, token)
    return load_credentials()


def require_automation_id(root: Path):
    config = load_project_config(root)
    automation_id = config.get('automation_id')
    if not automation_id:
        raise RobotError('Projeto ainda não foi publicado. Execute: nexus publish')
    return automation_id


def resolve_environment_id(base_url, token, key):
    environments = api_request(base_url, 'GET', '/environments', token=token)
    for item in environments or []:
        if item.get('key') == key:
            return item['id']
    raise RobotError(f'Ambiente {key} não encontrado ou não permitido para o seu perfil.')


def environment_use(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    # Validates access before persisting: /environments only lists environments the token's org membership can see.
    resolve_environment_id(base_url, token, args.key)
    set_default_environment(args.key)
    print_result({'default_environment': args.key}, args.json)


def resolve_version_id(base_url, token, automation_id, version):
    versions = api_request(base_url, 'GET', f'/automations/{automation_id}/versions', token=token)
    for item in versions or []:
        if item.get('version') == version and item.get('is_published'):
            return item['id']
    raise RobotError(f'Vers\u00e3o publicada {version} n\u00e3o encontrada para esta automa\u00e7\u00e3o.')


def set_current(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    version_id = args.version_id or resolve_version_id(base_url, token, automation_id, args.version or load_project_config(root).get('version'))
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/versions/{version_id}/current', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result({'automation_id': automation_id, 'version_id': version_id, 'environment': args.environment}, args.json)


def promote(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    version_id = None
    if args.version_id:
        version_id = args.version_id
    elif args.version:
        version_id = resolve_version_id(base_url, token, automation_id, args.version)
    from_environment_id = resolve_environment_id(base_url, token, args.from_environment)
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}/deployments/{args.to}', token=token,
                          payload={'version_id': version_id}, headers={'X-Environment-ID': from_environment_id})
    print_result(result, args.json)


def parse_kv_pairs(items):
    result = {}
    for item in items or []:
        key, sep, value = item.partition('=')
        if not sep or not key.strip():
            raise RobotError(f'Formato inválido "{item}", use CHAVE=VALOR.')
        result[key.strip()] = value
    return result


def credential_create(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'name': args.name, 'value': parse_kv_pairs(args.data)}
    if args.expires_at:
        payload['expires_at'] = args.expires_at
    if args.auto_rotate_days:
        payload['auto_rotate_days'] = args.auto_rotate_days
    if args.responsible_email:
        payload['responsible_user_email'] = args.responsible_email
    result = api_request(base_url, 'POST', '/credentials', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def credential_list(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', '/credentials', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def credential_bind(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}/credential-bindings', token=token,
                          payload={'credential_ids': args.credential_id or []}, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def trigger_create(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    if args.version_id:
        version_policy, version_id = 'PINNED', args.version_id
    elif args.version:
        version_policy, version_id = 'PINNED', resolve_version_id(base_url, token, automation_id, args.version)
    else:
        version_policy, version_id = 'CURRENT', None
    payload = {
        'name': args.name, 'type': args.type, 'version_policy': version_policy, 'version_id': version_id,
        'cron_expression': args.cron, 'timezone': args.timezone,
        'inputs': parse_kv_pairs(args.input), 'required_params': args.required_param or [],
        'timeout_seconds': args.timeout, 'credential_ids': args.credential_id or [],
        'require_auth': not args.no_require_auth, 'webhook_secret': args.webhook_secret,
    }
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/triggers', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def trigger_list(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', f'/automations/{automation_id}/triggers', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def queue_create(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'name': args.name, 'description': args.description}
    result = api_request(base_url, 'POST', '/queues', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def queue_bind(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'input_queue_id': args.input_queue_id, 'output_queue_id': args.output_queue_id}
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}/queues', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def queue_send(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'payload': parse_kv_pairs(args.data)}
    result = api_request(base_url, 'POST', f'/queues/{args.queue_id}/messages', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def execution_create(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'request_key': args.request_key, 'inputs': parse_kv_pairs(args.input), 'timeout_seconds': args.timeout}
    if args.version_id:
        payload['version_id'] = args.version_id
    else:
        payload['automation_id'] = require_automation_id(args.project.resolve())
    result = api_request(base_url, 'POST', '/executions', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def execution_list(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    query = f'?limit={args.limit}'
    if args.status:
        query += f'&status={args.status}'
    result = api_request(base_url, 'GET', f'/executions{query}', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def execution_logs(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    query = f'?limit={args.limit}'
    if args.level:
        query += f'&level={args.level}'
    result = api_request(base_url, 'GET', f'/executions/{args.execution_id}/logs{query}', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def automation_create(args):
    credentials = require_credentials()
    root = args.project.resolve()
    config = load_project_config(root)
    if config.get('automation_id'):
        raise RobotError('Este projeto já possui um automation_id em nexus.toml.')
    base_url, token = credentials['api_base_url'], credentials['token']
    created = api_request(base_url, 'POST', '/automations', token=token, payload={'name': args.name or config.get('name', root.name)})
    config_path = root / 'nexus.toml'
    content = config_path.read_text(encoding='utf-8')
    # Must be inserted before any [table] header, otherwise TOML parses it as a nested key.
    config_path.write_text(f'automation_id = "{created["id"]}"\n' + content, encoding='utf-8')
    print_result(created, args.json)

def automation_list(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', '/automations', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def automation_get(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', f'/automations/{automation_id}', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def automation_update(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {
        'name': args.name, 'description': args.description,
        'input_queue_id': args.input_queue_id, 'output_queue_id': args.output_queue_id,
        'business_area_id': args.business_area_id, 'priority': args.priority,
        'agent_ids': args.agent_id or [], 'credential_ids': args.credential_id or [],
    }
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def automation_delete(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    api_request(base_url, 'DELETE', f'/automations/{automation_id}', token=token, headers={'X-Environment-ID': environment_id})
    print_result({'automation_id': automation_id, 'deleted': True}, args.json)


def automation_archive(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/archive', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def automation_restore(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/restore', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def environment_list(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    result = api_request(base_url, 'GET', '/environments', token=token)
    print_result(result, args.json)


def credential_update(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'name': args.name, 'value': parse_kv_pairs(args.data)}
    if args.expires_at:
        payload['expires_at'] = args.expires_at
    if args.auto_rotate_days:
        payload['auto_rotate_days'] = args.auto_rotate_days
    if args.responsible_email:
        payload['responsible_user_email'] = args.responsible_email
    result = api_request(base_url, 'PUT', f'/credentials/{args.credential_id}', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def credential_delete(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    api_request(base_url, 'DELETE', f'/credentials/{args.credential_id}', token=token, headers={'X-Environment-ID': environment_id})
    print_result({'credential_id': args.credential_id, 'deleted': True}, args.json)


def webhook_update(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = args.automation_id or require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {
        'name': args.name, 'url': args.url, 'method': args.method,
        'trigger_on': args.trigger_on or ['SUCCEEDED', 'FAILED'],
        'credential_id': args.credential_id, 'is_active': not args.inactive,
    }
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}/webhooks/{args.webhook_id}', token=token,
                          payload=payload, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def webhook_delete(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = args.automation_id or require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    api_request(base_url, 'DELETE', f'/automations/{automation_id}/webhooks/{args.webhook_id}', token=token,
                headers={'X-Environment-ID': environment_id})
    print_result({'webhook_id': args.webhook_id, 'deleted': True}, args.json)


def queue_get(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', f'/queues/{args.queue_id}', token=token, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def queue_update(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {'name': args.name, 'description': args.description}
    result = api_request(base_url, 'PUT', f'/queues/{args.queue_id}', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def queue_delete(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    api_request(base_url, 'DELETE', f'/queues/{args.queue_id}', token=token, headers={'X-Environment-ID': environment_id})
    print_result({'queue_id': args.queue_id, 'deleted': True}, args.json)


def queue_messages(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    query = f'?limit={args.limit}&offset={args.offset}'
    if args.status:
        query += f'&status={args.status}'
    result = api_request(base_url, 'GET', f'/queues/{args.queue_id}/messages{query}', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def trigger_update(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = args.automation_id or require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    if args.version_id:
        version_policy, version_id = 'PINNED', args.version_id
    elif args.version:
        version_policy, version_id = 'PINNED', resolve_version_id(base_url, token, automation_id, args.version)
    else:
        version_policy, version_id = 'CURRENT', None
    payload = {
        'name': args.name, 'type': args.type, 'version_policy': version_policy, 'version_id': version_id,
        'cron_expression': args.cron, 'timezone': args.timezone,
        'inputs': parse_kv_pairs(args.input), 'required_params': args.required_param or [],
        'timeout_seconds': args.timeout, 'credential_ids': args.credential_id or [],
        'require_auth': not args.no_require_auth, 'webhook_secret': args.webhook_secret,
    }
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}/triggers/{args.trigger_id}', token=token,
                          payload=payload, headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def trigger_delete(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = args.automation_id or require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    api_request(base_url, 'DELETE', f'/automations/{automation_id}/triggers/{args.trigger_id}', token=token,
                headers={'X-Environment-ID': environment_id})
    print_result({'trigger_id': args.trigger_id, 'deleted': True}, args.json)


def trigger_toggle(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = args.automation_id or require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/triggers/{args.trigger_id}/toggle', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def execution_cancel(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'POST', f'/executions/{args.execution_id}/cancel', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def execution_retry(args):
    credentials = require_credentials()
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'POST', f'/executions/{args.execution_id}/retry', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def deployment_list(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    result = api_request(base_url, 'GET', f'/automations/{automation_id}/deployments', token=token)
    print_result(result, args.json)


def deployment_history(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    result = api_request(base_url, 'GET', f'/automations/{automation_id}/deployments/history', token=token)
    print_result(result, args.json)


def deployment_rollback(args):
    credentials = require_credentials()
    automation_id = args.automation_id or require_automation_id(args.project.resolve())
    base_url, token = credentials['api_base_url'], credentials['token']
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/deployments/history/{args.event_id}/rollback', token=token)
    print_result(result, args.json)


def bump(args):
    root = args.project.resolve()
    current = load_project_config(root).get('version')
    if not current:
        raise RobotError('nexus.toml não possui uma versão definida.')
    parts = [int(part) for part in current.split('.')]
    parts += [0] * (3 - len(parts))
    major, minor, patch = parts[:3]
    if args.part == 'major':
        major, minor, patch = major + 1, 0, 0
    elif args.part == 'minor':
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new_version = f'{major}.{minor}.{patch}'
    update_project_version(root, new_version)
    print_result({'previous_version': current, 'version': new_version}, args.json)


def doctor(args):
    root = args.project.resolve()
    checks = []

    def check(name, ok, detail=''):
        checks.append({'check': name, 'ok': ok, 'detail': detail})

    check('python_version', sys.version_info >= (3, 12), f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} (mínimo: 3.12)')

    config = None
    try:
        config = load_project_config(root)
        check('nexus_toml', True, str(root / 'nexus.toml'))
    except OSError:
        check('nexus_toml', False, 'nexus.toml não encontrado neste diretório (opcional fora de um projeto)')

    if config is not None:
        check('automation_id', bool(config.get('automation_id')), 'Execute: nexus publish ou nexus automation-create' if not config.get('automation_id') else '')
        sdk_min = (config.get('runtime') or {}).get('sdk_min')
        if sdk_min:
            check('sdk_min_compatible', tuple(int(part) for part in SDK_VERSION.split('.')) >= tuple(int(part) for part in sdk_min.split('.')),
                  f'SDK instalado: {SDK_VERSION}, mínimo exigido: {sdk_min}')

    credentials = load_credentials()
    check('authenticated', bool(credentials), 'Execute: nexus login' if not credentials else credentials.get('api_base_url'))
    if credentials:
        try:
            api_request(credentials['api_base_url'], 'GET', '/auth/profile', token=credentials['token'])
            check('api_reachable', True, credentials['api_base_url'])
        except RobotError as error:
            check('api_reachable', False, str(error))
        check('default_environment', bool(credentials.get('default_environment')),
              'Execute: nexus environment-use <AMBIENTE>' if not credentials.get('default_environment') else credentials.get('default_environment'))

    ok = all(item['ok'] for item in checks)
    print_result({'ok': ok, 'checks': checks}, args.json)
    return 0 if ok else 1


def pull(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = args.automation_id or require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    if args.version_id:
        version_id = args.version_id
    else:
        version_id = resolve_version_id(base_url, token, automation_id, args.version)
    content = api_download(base_url, f'/automations/{automation_id}/versions/{version_id}/download', token=token)

    if args.zip_only:
        output_path = Path(args.output) if args.output else root / f'{automation_id}-{version_id}.zip'
        output_path.write_bytes(content)
        print_result({'automation_id': automation_id, 'version_id': version_id, 'saved_to': str(output_path)}, args.json)
        return

    target = Path(args.output).resolve() if args.output else root
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for item in archive.infolist():
            member_path = PurePosixPath(item.filename)
            if member_path.is_absolute() or '..' in member_path.parts:
                raise RobotError('Pacote contém caminhos de arquivo inseguros.')
            destination = target / member_path
            if destination.exists() and not item.is_dir() and not args.force:
                raise RobotError(f'Arquivo já existe: {destination}. Use --force para sobrescrever.')
        archive.extractall(target)
    ensure_automation_id(target, automation_id)
    print_result({'automation_id': automation_id, 'version_id': version_id, 'extracted_to': str(target)}, args.json)


def ensure_automation_id(root: Path, automation_id: str):
    # Grava o automation_id no nexus.toml extraído para que comandos seguintes não exijam --automation-id.
    config_path = root / 'nexus.toml'
    if not config_path.exists():
        config_path.write_text(f'automation_id = "{automation_id}"\n', encoding='utf-8')
        return
    content = config_path.read_text(encoding='utf-8')
    if re.search(r'(?m)^automation_id\s*=', content):
        return
    config_path.write_text(f'automation_id = "{automation_id}"\n' + content, encoding='utf-8')

def environment_set(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    headers = {'X-Environment-ID': environment_id}
    merged = {} if args.replace else {
        item['name']: item['value']
        for item in api_request(base_url, 'GET', f'/automations/{automation_id}/environment', token=token, headers=headers)['variables']
    }
    merged.update(parse_kv_pairs(args.set))
    variables = [{'name': key, 'value': value} for key, value in merged.items()]
    result = api_request(base_url, 'PUT', f'/automations/{automation_id}/environment', token=token,
                          payload={'variables': variables}, headers=headers)
    print_result(result, args.json)


def environment_get(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', f'/automations/{automation_id}/environment', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def webhook_create(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    payload = {
        'name': args.name, 'url': args.url, 'method': args.method,
        'trigger_on': args.trigger_on or ['SUCCEEDED', 'FAILED'],
        'credential_id': args.credential_id, 'is_active': not args.inactive,
    }
    result = api_request(base_url, 'POST', f'/automations/{automation_id}/webhooks', token=token, payload=payload,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def webhook_list(args):
    credentials = require_credentials()
    root = args.project.resolve()
    automation_id = require_automation_id(root)
    base_url, token = credentials['api_base_url'], credentials['token']
    environment_id = resolve_environment_id(base_url, token, args.environment)
    result = api_request(base_url, 'GET', f'/automations/{automation_id}/webhooks', token=token,
                          headers={'X-Environment-ID': environment_id})
    print_result(result, args.json)


def publish_project(args):
    credentials = require_credentials()
    root = args.project.resolve()
    if args.version:
        update_project_version(root, validate_version(args.version))
    validate_project(root, check_environment=False)
    config = load_project_config(root)
    automation_id = config.get('automation_id')
    base_url = credentials['api_base_url']
    token = credentials['token']
    if not automation_id:
        created = api_request(base_url, 'POST', '/automations', token=token, payload={'name': config.get('name', root.name)})
        automation_id = created['id']
        config_path = root / 'nexus.toml'
        content = config_path.read_text(encoding='utf-8')
        # Must be inserted before any [table] header, otherwise TOML parses it as a nested key.
        config_path.write_text(f'automation_id = "{automation_id}"\n' + content, encoding='utf-8')
        print(f'Automação criada: {automation_id}')
    script = entrypoint(root)
    name = safe_zip_name(config.get('name', root.name))
    version = validate_version(config.get('version', '1.0.0'))
    with tempfile.TemporaryDirectory(prefix='nexus-publish-') as directory:
        args.project = root
        args.output = Path(directory) / f'{name}-{version}.zip'
        package_project(args)
        package_bytes = args.output.read_bytes()
    uploaded = api_upload(
        base_url, f'/automations/{automation_id}/versions/upload', token,
        fields={'version': version, 'entrypoint': script.relative_to(root).as_posix(), 'change_type': 'FEATURE'},
        file_field='package', file_name=f'{name}-{version}.zip', file_bytes=package_bytes,
    )
    if args.publish or args.environment:
        api_request(base_url, 'POST', f'/automations/{automation_id}/versions/{uploaded["id"]}/publish', token=token)
    if args.environment:
        environment_id = resolve_environment_id(base_url, token, args.environment)
        api_request(base_url, 'POST', f'/automations/{automation_id}/versions/{uploaded["id"]}/current', token=token,
                    headers={'X-Environment-ID': environment_id})
    print_result({'automation_id': automation_id, 'version_id': uploaded['id'], 'version': version,
                  'published': bool(args.publish or args.environment), 'environment': args.environment}, args.json)


def main(argv=None):
    # Falls back to DEVELOPMENT until the user runs `nexus environment-use`.
    default_environment = (load_credentials() or {}).get('default_environment', 'DEVELOPMENT')
    parser = argparse.ArgumentParser(prog='nexus', description='Crie e teste seus bots Nexus localmente.')
    parser.add_argument('--json', action='store_true', help='Exibir a resposta bruta em JSON em vez de uma tabela/lista legível')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Criar um projeto em uma nova pasta')
    init.add_argument('directory', type=Path)
    venv = sub.add_parser('venv', help='Criar ambiente virtual do projeto e instalar dependências')
    venv.add_argument('--project', type=Path, default=Path('.'))
    venv.add_argument('--path', type=Path, help='Caminho do ambiente virtual; padrão: <projeto>/.venv')
    venv.add_argument('--recreate', action='store_true', help='Remover e criar novamente se o ambiente já existir')
    venv.add_argument('--install-requirements', action='store_true', help='Além do SDK, instalar todas as dependências do requirements.txt')
    venv.add_argument('--shell', action='store_true', help='Abrir uma sessão de terminal já configurada com o ambiente virtual')
    run = sub.add_parser('run', help='Executar localmente sem API ou conta')
    run.add_argument('--project', type=Path, default=Path('.'))
    run.add_argument('--inputs', type=Path)
    run.add_argument('--fixtures', type=Path, help='Entradas, credenciais fictícias e filas para teste local')
    run.add_argument('--output', type=Path)
    run.add_argument('--publications-output', type=Path, help='Salvar publicações simuladas em JSON (sem enviar à API)')
    run.add_argument('--debug', action='store_true', help='Exibir traceback completo no terminal local')
    inspect = sub.add_parser('inspect', help='Mostrar os dados para cadastrar uma versão no painel')
    inspect.add_argument('--project', type=Path, default=Path('.'))
    package = sub.add_parser('package', help='Criar ZIP reproduzível para publicar no Nexus')
    package.add_argument('--project', type=Path, default=Path('.'))
    package.add_argument('--version', help='Atualizar nexus.toml e gerar ZIP com esta versão')
    package.add_argument('--output', type=Path, help='Caminho explícito; padrão: <projeto>/dist/<nome>-<versão>.zip')
    validate = sub.add_parser('validate', help='Validar configuração sem executar o código do bot')
    validate.add_argument('--project', type=Path, default=Path('.'))
    validate.add_argument('--strict', action='store_true', help='Tratar avisos como erros')
    doctor_parser = sub.add_parser('doctor', help='Diagnosticar ambiente local, projeto e autenticação')
    doctor_parser.add_argument('--project', type=Path, default=Path('.'))
    bump_parser = sub.add_parser('bump', help='Incrementar a versão em nexus.toml (semver)')
    bump_parser.add_argument('--project', type=Path, default=Path('.'))
    bump_parser.add_argument('--part', default='patch', choices=['major', 'minor', 'patch'])
    login_parser = sub.add_parser('login', help='Autenticar o CLI com um Personal Access Token')
    login_parser.add_argument('--api-url', default=os.environ.get('NEXUS_API_URL', 'https://api.nexusorchestrator.com'), help='URL base da API Nexus')
    login_parser.add_argument('--token', help='Personal Access Token (nxs_...); se omitido, será solicitado interativamente')
    login_parser.add_argument('--profile', help='Nome do perfil (ex: local, staging-clienteA, prod-clienteB); padrão: derivado da URL da API')
    logout_parser = sub.add_parser('logout', help='Remover as credenciais salvas localmente')
    logout_parser.add_argument('--profile', help='Remover apenas este perfil; padrão: remove todos')
    sub.add_parser('whoami', help='Mostrar a conta autenticada atualmente')
    sub.add_parser('profile-list', help='Listar os perfis (URL + conta) com login salvo')
    profile_use_parser = sub.add_parser('profile-use', help='Trocar de perfil sem precisar logar de novo')
    profile_use_parser.add_argument('name', help='Nome do perfil, veja em: nexus profile-list')
    publish = sub.add_parser('publish', help='Empacotar e enviar uma nova versão para o Nexus')
    publish.add_argument('--project', type=Path, default=Path('.'))
    publish.add_argument('--version', help='Atualizar nexus.toml e publicar com esta versão')
    publish.add_argument('--publish', action='store_true', help='Publicar a versão imediatamente após o envio')
    publish.add_argument('--environment', choices=['DEVELOPMENT', 'STAGING', 'PRODUCTION'],
                          help='Publicar e já definir esta versão como atual no ambiente informado (implica --publish)')
    set_current_parser = sub.add_parser('set-current', help='Definir a versão ativa da automação em um ambiente')
    set_current_parser.add_argument('--project', type=Path, default=Path('.'))
    set_current_parser.add_argument('--environment', default=default_environment, choices=['DEVELOPMENT', 'STAGING', 'PRODUCTION'], help='Ambiente onde a versão será definida como atual')
    set_current_group = set_current_parser.add_mutually_exclusive_group(required=True)
    set_current_group.add_argument('--version', help='Versão publicada (ex: 1.0.1)')
    set_current_group.add_argument('--version-id', help='ID da versão publicada')
    promote_parser = sub.add_parser('promote', help='Promover uma versão para o próximo ambiente do pipeline')
    promote_parser.add_argument('--project', type=Path, default=Path('.'))
    promote_parser.add_argument('--from', dest='from_environment', default=default_environment if default_environment != 'PRODUCTION' else 'DEVELOPMENT', choices=['DEVELOPMENT', 'STAGING'], help='Ambiente de origem da promoção')
    promote_parser.add_argument('--to', required=True, choices=['STAGING', 'PRODUCTION'], help='Ambiente de destino da promoção')
    promote_group = promote_parser.add_mutually_exclusive_group()
    promote_group.add_argument('--version', help='Versão publicada (ex: 1.0.1)')
    promote_group.add_argument('--version-id', help='ID da versão publicada')

    environment_choices = ['DEVELOPMENT', 'STAGING', 'PRODUCTION']

    environment_use_parser = sub.add_parser('environment-use', help='Definir o ambiente padrão usado quando --environment não é informado (valida acesso antes de salvar)')
    environment_use_parser.add_argument('key', choices=environment_choices)

    automation_create_parser = sub.add_parser('automation-create', help='Criar a automação no Nexus sem publicar nenhuma versão')
    automation_create_parser.add_argument('--project', type=Path, default=Path('.'))
    automation_create_parser.add_argument('--name', help='Nome da automação (padrão: nome do projeto)')

    automation_list_parser = sub.add_parser('automation-list', help='Listar automações do ambiente')
    automation_list_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    automation_get_parser = sub.add_parser('automation-get', help='Ver detalhes da automação')
    automation_get_parser.add_argument('--project', type=Path, default=Path('.'))
    automation_get_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    automation_get_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    automation_update_parser = sub.add_parser('automation-update', help='Atualizar a automação (substitui todos os campos, como no painel)')
    automation_update_parser.add_argument('--project', type=Path, default=Path('.'))
    automation_update_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    automation_update_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    automation_update_parser.add_argument('--name', required=True)
    automation_update_parser.add_argument('--description')
    automation_update_parser.add_argument('--input-queue-id')
    automation_update_parser.add_argument('--output-queue-id')
    automation_update_parser.add_argument('--business-area-id')
    automation_update_parser.add_argument('--priority', type=int, default=0)
    automation_update_parser.add_argument('--agent-id', action='append', help='ID de VM/agente vinculado; pode repetir')
    automation_update_parser.add_argument('--credential-id', action='append', help='ID de credencial vinculada; pode repetir')

    automation_delete_parser = sub.add_parser('automation-delete', help='Remover a automação')
    automation_delete_parser.add_argument('--project', type=Path, default=Path('.'))
    automation_delete_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    automation_delete_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    automation_archive_parser = sub.add_parser('automation-archive', help='Arquivar a automação')
    automation_archive_parser.add_argument('--project', type=Path, default=Path('.'))
    automation_archive_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    automation_archive_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    automation_restore_parser = sub.add_parser('automation-restore', help='Restaurar uma automação arquivada')
    automation_restore_parser.add_argument('--project', type=Path, default=Path('.'))
    automation_restore_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    automation_restore_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    sub.add_parser('environment-list', help='Listar os ambientes disponíveis para o seu perfil')

    deployment_list_parser = sub.add_parser('deployment-list', help='Ver a versão implantada da automação em cada ambiente')
    deployment_list_parser.add_argument('--project', type=Path, default=Path('.'))
    deployment_list_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')

    deployment_history_parser = sub.add_parser('deployment-history', help='Ver o histórico de promoções/implantações da automação')
    deployment_history_parser.add_argument('--project', type=Path, default=Path('.'))
    deployment_history_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')

    deployment_rollback_parser = sub.add_parser('deployment-rollback', help='Reverter uma promoção usando o ID de um evento de deployment-history')
    deployment_rollback_parser.add_argument('--project', type=Path, default=Path('.'))
    deployment_rollback_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    deployment_rollback_parser.add_argument('--event-id', required=True, help='ID do evento (coluna id de deployment-history)')

    pull_parser = sub.add_parser('pull', help='Baixar (e extrair) uma versão publicada da automação para o projeto local')
    pull_parser.add_argument('--project', type=Path, default=Path('.'), help='Diretório do projeto (usado para ler o automation_id em nexus.toml, salvo se --automation-id for informado)')
    pull_parser.add_argument('--automation-id', help='ID da automação (padrão: automation_id do nexus.toml do projeto)')
    pull_version_group = pull_parser.add_mutually_exclusive_group(required=True)
    pull_version_group.add_argument('--version', help='Versão publicada a baixar (ex: 1.0.1)')
    pull_version_group.add_argument('--version-id', help='ID da versão a baixar')
    pull_parser.add_argument('--output', help='Pasta de destino (ou caminho do .zip com --zip-only); padrão: --project')
    pull_parser.add_argument('--zip-only', action='store_true', help='Salvar apenas o .zip sem extrair')
    pull_parser.add_argument('--force', action='store_true', help='Sobrescrever arquivos existentes ao extrair')

    credential_create_parser = sub.add_parser('credential-create', help='Criar uma credencial no ambiente selecionado')
    credential_create_parser.add_argument('--name', required=True)
    credential_create_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    credential_create_parser.add_argument('--data', action='append', metavar='CHAVE=VALOR', required=True, help='Par chave=valor; pode repetir')
    credential_create_parser.add_argument('--expires-at', help='Data ISO 8601 de expiração')
    credential_create_parser.add_argument('--auto-rotate-days', type=int)
    credential_create_parser.add_argument('--responsible-email', help='E-mail do responsável pela credencial')

    credential_list_parser = sub.add_parser('credential-list', help='Listar credenciais do ambiente')
    credential_list_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    credential_bind_parser = sub.add_parser('credential-bind', help='Associar credenciais à automação do projeto')
    credential_bind_parser.add_argument('--project', type=Path, default=Path('.'))
    credential_bind_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    credential_bind_parser.add_argument('--credential-id', action='append', required=True, help='ID da credencial; pode repetir')

    credential_update_parser = sub.add_parser('credential-update', help='Atualizar uma credencial (substitui todos os valores, como no painel)')
    credential_update_parser.add_argument('--credential-id', required=True)
    credential_update_parser.add_argument('--name', required=True)
    credential_update_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    credential_update_parser.add_argument('--data', action='append', metavar='CHAVE=VALOR', required=True, help='Par chave=valor; pode repetir')
    credential_update_parser.add_argument('--expires-at', help='Data ISO 8601 de expiração')
    credential_update_parser.add_argument('--auto-rotate-days', type=int)
    credential_update_parser.add_argument('--responsible-email', help='E-mail do responsável pela credencial')

    credential_delete_parser = sub.add_parser('credential-delete', help='Remover uma credencial')
    credential_delete_parser.add_argument('--credential-id', required=True)
    credential_delete_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    trigger_create_parser = sub.add_parser('trigger-create', help='Criar um disparador (agendamento ou webhook)')
    trigger_create_parser.add_argument('--project', type=Path, default=Path('.'))
    trigger_create_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    trigger_create_parser.add_argument('--name', required=True)
    trigger_create_parser.add_argument('--type', required=True, choices=['SCHEDULE', 'WEBHOOK'])
    trigger_create_parser.add_argument('--cron', help='Expressão cron; obrigatório para type=SCHEDULE')
    trigger_create_parser.add_argument('--timezone', default='UTC')
    trigger_create_parser.add_argument('--version', help='Versão publicada fixa (padrão: acompanhar a versão atual do ambiente)')
    trigger_create_parser.add_argument('--version-id', help='ID da versão publicada fixa')
    trigger_create_parser.add_argument('--input', action='append', metavar='CHAVE=VALOR', help='Parâmetro fixo de entrada; pode repetir')
    trigger_create_parser.add_argument('--required-param', action='append', help='Nome de parâmetro obrigatório no webhook; pode repetir')
    trigger_create_parser.add_argument('--timeout', type=int, default=300)
    trigger_create_parser.add_argument('--credential-id', action='append', help='ID de credencial vinculada; pode repetir')
    trigger_create_parser.add_argument('--no-require-auth', action='store_true', help='Desativar autenticação do webhook')
    trigger_create_parser.add_argument('--webhook-secret', help='Segredo do webhook (gerado automaticamente se omitido)')

    trigger_list_parser = sub.add_parser('trigger-list', help='Listar disparadores da automação do projeto')
    trigger_list_parser.add_argument('--project', type=Path, default=Path('.'))
    trigger_list_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    trigger_update_parser = sub.add_parser('trigger-update', help='Atualizar um disparador (substitui todos os campos, como no painel)')
    trigger_update_parser.add_argument('--project', type=Path, default=Path('.'))
    trigger_update_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    trigger_update_parser.add_argument('--trigger-id', required=True)
    trigger_update_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    trigger_update_parser.add_argument('--name', required=True)
    trigger_update_parser.add_argument('--type', required=True, choices=['SCHEDULE', 'WEBHOOK'])
    trigger_update_parser.add_argument('--cron', help='Expressão cron; obrigatório para type=SCHEDULE')
    trigger_update_parser.add_argument('--timezone', default='UTC')
    trigger_update_parser.add_argument('--version', help='Versão publicada fixa (padrão: acompanhar a versão atual do ambiente)')
    trigger_update_parser.add_argument('--version-id', help='ID da versão publicada fixa')
    trigger_update_parser.add_argument('--input', action='append', metavar='CHAVE=VALOR', help='Parâmetro fixo de entrada; pode repetir')
    trigger_update_parser.add_argument('--required-param', action='append', help='Nome de parâmetro obrigatório no webhook; pode repetir')
    trigger_update_parser.add_argument('--timeout', type=int, default=300)
    trigger_update_parser.add_argument('--credential-id', action='append', help='ID de credencial vinculada; pode repetir')
    trigger_update_parser.add_argument('--no-require-auth', action='store_true', help='Desativar autenticação do webhook')
    trigger_update_parser.add_argument('--webhook-secret', help='Segredo do webhook (gerado automaticamente se omitido)')

    trigger_delete_parser = sub.add_parser('trigger-delete', help='Remover um disparador')
    trigger_delete_parser.add_argument('--project', type=Path, default=Path('.'))
    trigger_delete_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    trigger_delete_parser.add_argument('--trigger-id', required=True)
    trigger_delete_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    trigger_toggle_parser = sub.add_parser('trigger-toggle', help='Ativar/pausar um disparador')
    trigger_toggle_parser.add_argument('--project', type=Path, default=Path('.'))
    trigger_toggle_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    trigger_toggle_parser.add_argument('--trigger-id', required=True)
    trigger_toggle_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    queue_create_parser = sub.add_parser('queue-create', help='Criar uma fila no ambiente selecionado')
    queue_create_parser.add_argument('--name', required=True)
    queue_create_parser.add_argument('--description')
    queue_create_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    queue_bind_parser = sub.add_parser('queue-bind', help='Associar filas de entrada/saída à automação do projeto')
    queue_bind_parser.add_argument('--project', type=Path, default=Path('.'))
    queue_bind_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    queue_bind_parser.add_argument('--input-queue-id')
    queue_bind_parser.add_argument('--output-queue-id')

    queue_send_parser = sub.add_parser('queue-send', help='Publicar uma mensagem em uma fila')
    queue_send_parser.add_argument('--queue-id', required=True)
    queue_send_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    queue_send_parser.add_argument('--data', action='append', metavar='CHAVE=VALOR', required=True, help='Par chave=valor; pode repetir')

    queue_get_parser = sub.add_parser('queue-get', help='Ver detalhes de uma fila')
    queue_get_parser.add_argument('--queue-id', required=True)
    queue_get_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    queue_update_parser = sub.add_parser('queue-update', help='Atualizar nome/descrição de uma fila')
    queue_update_parser.add_argument('--queue-id', required=True)
    queue_update_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    queue_update_parser.add_argument('--name', required=True)
    queue_update_parser.add_argument('--description')

    queue_delete_parser = sub.add_parser('queue-delete', help='Remover uma fila (sem vínculos ou histórico de mensagens)')
    queue_delete_parser.add_argument('--queue-id', required=True)
    queue_delete_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    queue_messages_parser = sub.add_parser('queue-messages', help='Listar mensagens de uma fila')
    queue_messages_parser.add_argument('--queue-id', required=True)
    queue_messages_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    queue_messages_parser.add_argument('--status')
    queue_messages_parser.add_argument('--limit', type=int, default=100)
    queue_messages_parser.add_argument('--offset', type=int, default=0)

    execution_create_parser = sub.add_parser('execution-create', help='Disparar uma nova execução')
    execution_create_parser.add_argument('--project', type=Path, default=Path('.'))
    execution_create_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    execution_create_parser.add_argument('--version-id', help='ID de versão específica (padrão: versão atual do ambiente)')
    execution_create_parser.add_argument('--request-key', required=True, help='Chave de idempotência da requisição')
    execution_create_parser.add_argument('--input', action='append', metavar='CHAVE=VALOR', help='Parâmetro de entrada; pode repetir')
    execution_create_parser.add_argument('--timeout', type=int, default=300)

    execution_list_parser = sub.add_parser('execution-list', help='Listar execuções do ambiente')
    execution_list_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    execution_list_parser.add_argument('--status')
    execution_list_parser.add_argument('--limit', type=int, default=50)

    execution_logs_parser = sub.add_parser('execution-logs', help='Ver logs de uma execução')
    execution_logs_parser.add_argument('--execution-id', required=True)
    execution_logs_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    execution_logs_parser.add_argument('--level')
    execution_logs_parser.add_argument('--limit', type=int, default=50)

    execution_cancel_parser = sub.add_parser('execution-cancel', help='Cancelar uma execução em fila ou em andamento')
    execution_cancel_parser.add_argument('--execution-id', required=True)
    execution_cancel_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    execution_retry_parser = sub.add_parser('execution-retry', help='Repetir uma execução criando uma nova a partir dela')
    execution_retry_parser.add_argument('--execution-id', required=True)
    execution_retry_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    environment_set_parser = sub.add_parser('environment-set', help='Definir variáveis de ambiente (ENV) da automação')
    environment_set_parser.add_argument('--project', type=Path, default=Path('.'))
    environment_set_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    environment_set_parser.add_argument('--set', action='append', metavar='NOME=VALOR', required=True, dest='set', help='Par nome=valor; pode repetir. Por padrão faz merge com as variáveis existentes (mesmo nome sobrescreve o valor).')
    environment_set_parser.add_argument('--replace', action='store_true', help='Substituir todas as variáveis existentes em vez de fazer merge')

    environment_get_parser = sub.add_parser('environment-get', help='Ver variáveis de ambiente (ENV) da automação')
    environment_get_parser.add_argument('--project', type=Path, default=Path('.'))
    environment_get_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    webhook_create_parser = sub.add_parser('webhook-create', help='Criar uma ação de saída (webhook de execução)')
    webhook_create_parser.add_argument('--project', type=Path, default=Path('.'))
    webhook_create_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    webhook_create_parser.add_argument('--name', required=True)
    webhook_create_parser.add_argument('--url', required=True)
    webhook_create_parser.add_argument('--method', default='POST', choices=['POST', 'PUT', 'PATCH', 'GET'])
    webhook_create_parser.add_argument('--trigger-on', action='append', choices=['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'], help='Status que dispara o webhook; pode repetir (padrão: SUCCEEDED e FAILED)')
    webhook_create_parser.add_argument('--credential-id', help='Credencial usada para autenticar a chamada de saída')
    webhook_create_parser.add_argument('--inactive', action='store_true', help='Criar desativado')

    webhook_list_parser = sub.add_parser('webhook-list', help='Listar ações de saída (webhooks) da automação')
    webhook_list_parser.add_argument('--project', type=Path, default=Path('.'))
    webhook_list_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    webhook_update_parser = sub.add_parser('webhook-update', help='Atualizar uma ação de saída (webhook de execução)')
    webhook_update_parser.add_argument('--project', type=Path, default=Path('.'))
    webhook_update_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    webhook_update_parser.add_argument('--webhook-id', required=True)
    webhook_update_parser.add_argument('--environment', default=default_environment, choices=environment_choices)
    webhook_update_parser.add_argument('--name', required=True)
    webhook_update_parser.add_argument('--url', required=True)
    webhook_update_parser.add_argument('--method', default='POST', choices=['POST', 'PUT', 'PATCH', 'GET'])
    webhook_update_parser.add_argument('--trigger-on', action='append', choices=['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'], help='Status que dispara o webhook; pode repetir (padrão: SUCCEEDED e FAILED)')
    webhook_update_parser.add_argument('--credential-id', help='Credencial usada para autenticar a chamada de saída')
    webhook_update_parser.add_argument('--inactive', action='store_true', help='Desativar o webhook')

    webhook_delete_parser = sub.add_parser('webhook-delete', help='Remover uma ação de saída (webhook)')
    webhook_delete_parser.add_argument('--project', type=Path, default=Path('.'))
    webhook_delete_parser.add_argument('--automation-id', help='padrão: automation_id do nexus.toml do projeto')
    webhook_delete_parser.add_argument('--webhook-id', required=True)
    webhook_delete_parser.add_argument('--environment', default=default_environment, choices=environment_choices)

    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            init_project(args.directory)
        elif args.command == 'venv':
            create_virtualenv(args)
        elif args.command == 'run':
            return run_local(args)
        elif args.command == 'validate':
            report = validate_project(args.project)
            print_result(report, args.json)
            return 1 if args.strict and report['warnings'] else 0
        elif args.command == 'doctor':
            return doctor(args)
        elif args.command == 'bump':
            bump(args)
        elif args.command == 'inspect':
            script = entrypoint(args.project)
            print_result({'package_path': script.relative_to(args.project.resolve()).as_posix(), 'entrypoint': script.name,
                          'checksum': hashlib.sha256(script.read_bytes()).hexdigest()}, args.json)
        elif args.command == 'login':
            login(args)
        elif args.command == 'logout':
            logout(args)
        elif args.command == 'whoami':
            whoami(args)
        elif args.command == 'profile-list':
            profile_list(args)
        elif args.command == 'profile-use':
            profile_use(args)
        elif args.command == 'publish':
            publish_project(args)
        elif args.command == 'set-current':
            set_current(args)
        elif args.command == 'promote':
            promote(args)
        elif args.command == 'credential-create':
            credential_create(args)
        elif args.command == 'credential-list':
            credential_list(args)
        elif args.command == 'credential-bind':
            credential_bind(args)
        elif args.command == 'credential-update':
            credential_update(args)
        elif args.command == 'credential-delete':
            credential_delete(args)
        elif args.command == 'trigger-create':
            trigger_create(args)
        elif args.command == 'trigger-list':
            trigger_list(args)
        elif args.command == 'trigger-update':
            trigger_update(args)
        elif args.command == 'trigger-delete':
            trigger_delete(args)
        elif args.command == 'trigger-toggle':
            trigger_toggle(args)
        elif args.command == 'queue-create':
            queue_create(args)
        elif args.command == 'queue-bind':
            queue_bind(args)
        elif args.command == 'queue-send':
            queue_send(args)
        elif args.command == 'queue-get':
            queue_get(args)
        elif args.command == 'queue-update':
            queue_update(args)
        elif args.command == 'queue-delete':
            queue_delete(args)
        elif args.command == 'queue-messages':
            queue_messages(args)
        elif args.command == 'execution-create':
            execution_create(args)
        elif args.command == 'execution-list':
            execution_list(args)
        elif args.command == 'execution-logs':
            execution_logs(args)
        elif args.command == 'execution-cancel':
            execution_cancel(args)
        elif args.command == 'execution-retry':
            execution_retry(args)
        elif args.command == 'environment-use':
            environment_use(args)
        elif args.command == 'environment-list':
            environment_list(args)
        elif args.command == 'automation-create':
            automation_create(args)
        elif args.command == 'automation-list':
            automation_list(args)
        elif args.command == 'automation-get':
            automation_get(args)
        elif args.command == 'automation-update':
            automation_update(args)
        elif args.command == 'automation-delete':
            automation_delete(args)
        elif args.command == 'automation-archive':
            automation_archive(args)
        elif args.command == 'automation-restore':
            automation_restore(args)
        elif args.command == 'deployment-list':
            deployment_list(args)
        elif args.command == 'deployment-history':
            deployment_history(args)
        elif args.command == 'deployment-rollback':
            deployment_rollback(args)
        elif args.command == 'pull':
            pull(args)
        elif args.command == 'environment-set':
            environment_set(args)
        elif args.command == 'environment-get':
            environment_get(args)
        elif args.command == 'webhook-create':
            webhook_create(args)
        elif args.command == 'webhook-list':
            webhook_list(args)
        elif args.command == 'webhook-update':
            webhook_update(args)
        elif args.command == 'webhook-delete':
            webhook_delete(args)
        else:
            package_project(args)
        return 0
    except (OSError, ValueError, RobotError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
