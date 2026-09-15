import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from .core import RobotError, read_object, write_object
from .project import validate_project
from .credentials import save_credentials, load_credentials, clear_credentials
from .http import api_request, api_upload

SDK_VERSION = '0.4.2'
SDK_RUNTIME_MIN = '0.4.0'
SDK_REQUIREMENT = f'nexus-sdk @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v{SDK_VERSION}.zip'
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
    (path / 'requirements.txt').write_text(f'{SDK_REQUIREMENT}\n# Adicione abaixo apenas as bibliotecas usadas pelo seu bot.\n', encoding='utf-8')
    (path / '.gitignore').write_text('.venv/\n__pycache__/\nresult.json\n.env\n*.local.json\ndist/\n', encoding='utf-8')
    (path / 'README.md').write_text(f'# Framework Nexus\n\nExecute localmente:\n\n```sh\nnexus run --inputs inputs.json\n```\n\nEmpacote para publicar no Nexus:\n\n```sh\nnexus package\n```\n\nO `requirements.txt` já fixa o SDK na tag pública `{SDK_VERSION}`:\n\n```txt\n{SDK_REQUIREMENT}\n```\n\nAdicione suas dependências de automação abaixo dessa linha.\n', encoding='utf-8')
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


def run_local(args):
    script = entrypoint(args.project)
    fixtures = read_object(args.fixtures, limit=512 * 1024) if args.fixtures else {}
    for field in ('inputs', 'credentials', 'queues'):
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
    with tempfile.TemporaryDirectory(prefix='nexus-local-') as directory:
        work = Path(directory)
        source = work / 'inputs.json'
        result_file = work / 'result.json'
        secrets_file = work / 'secrets.json'
        queue_file = work / 'queues.json'
        write_object(source, inputs)
        write_object(secrets_file, fixtures.get('credentials', {}), limit=256 * 1024)
        write_object(queue_file, fixtures.get('queues', {}))
        environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'LANG') if key in os.environ}
        environment.update(HOME=str(work), TMPDIR=str(work), TEMP=str(work), TMP=str(work),
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


def login(args):
    base_url = args.api_url.rstrip('/')
    token = args.token or input('Cole seu Personal Access Token (nxs_...): ').strip()
    if not token.startswith('nxs_'):
        raise RobotError('Token inválido. Gere um token pessoal em Perfil > Tokens de API.')
    profile = api_request(base_url, 'GET', '/auth/profile', token=token)
    tokens = api_request(base_url, 'GET', '/auth/tokens', token=token)
    organization_id, organization_name = None, None
    matching = [item for item in (tokens or []) if item.get('prefix') and token.startswith(item['prefix'])]
    if matching:
        organization_id = matching[0]['organization_id']
        organization_name = matching[0]['organization_name']
    save_credentials(base_url, token, organization_id, organization_name)
    print(json.dumps({'logged_in_as': profile.get('email'), 'api_url': base_url, 'organization': organization_name}, ensure_ascii=False, indent=2))


def logout(args):
    clear_credentials()
    print('Sessão local removida.')


def whoami(args):
    credentials = load_credentials()
    if not credentials:
        raise RobotError('Você não está autenticado. Execute: nexus login')
    profile = api_request(credentials['api_base_url'], 'GET', '/auth/profile', token=credentials['token'])
    print(json.dumps({'email': profile.get('email'), 'api_url': credentials['api_base_url'],
                      'organization': credentials.get('organization_name')}, ensure_ascii=False, indent=2))


def publish_project(args):
    credentials = load_credentials()
    if not credentials:
        raise RobotError('Você não está autenticado. Execute: nexus login')
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
        config_path.write_text(content + f'\nautomation_id = "{automation_id}"\n', encoding='utf-8')
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
    if args.publish:
        api_request(base_url, 'POST', f'/automations/{automation_id}/versions/{uploaded["id"]}/publish', token=token)
    print(json.dumps({'automation_id': automation_id, 'version_id': uploaded['id'], 'version': version,
                      'published': bool(args.publish)}, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nexus', description='Crie e teste seus bots Nexus localmente.')
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
    login_parser = sub.add_parser('login', help='Autenticar o CLI com um Personal Access Token')
    login_parser.add_argument('--api-url', default=os.environ.get('NEXUS_API_URL', 'https://api.nexusorchestrator.com'), help='URL base da API Nexus')
    login_parser.add_argument('--token', help='Personal Access Token (nxs_...); se omitido, será solicitado interativamente')
    sub.add_parser('logout', help='Remover as credenciais salvas localmente')
    sub.add_parser('whoami', help='Mostrar a conta autenticada atualmente')
    publish = sub.add_parser('publish', help='Empacotar e enviar uma nova versão para o Nexus')
    publish.add_argument('--project', type=Path, default=Path('.'))
    publish.add_argument('--version', help='Atualizar nexus.toml e publicar com esta versão')
    publish.add_argument('--publish', action='store_true', help='Publicar a versão imediatamente após o envio')
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
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if args.strict and report['warnings'] else 0
        elif args.command == 'inspect':
            script = entrypoint(args.project)
            print(json.dumps({'package_path': script.relative_to(args.project.resolve()).as_posix(), 'entrypoint': script.name,
                              'checksum': hashlib.sha256(script.read_bytes()).hexdigest()}, indent=2))
        elif args.command == 'login':
            login(args)
        elif args.command == 'logout':
            logout(args)
        elif args.command == 'whoami':
            whoami(args)
        elif args.command == 'publish':
            publish_project(args)
        else:
            package_project(args)
        return 0
    except (OSError, ValueError, RobotError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
