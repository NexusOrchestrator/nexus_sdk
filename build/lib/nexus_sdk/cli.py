import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from .core import RobotError, read_object, write_object
from .project import validate_project

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
    (path / 'nexus.toml').write_text('name = ' + json.dumps(path.resolve().name, ensure_ascii=False) + '\nentrypoint = "bot.py"\nversion = "1.0.0"\ncredentials = []\n\n[runtime]\npython = "3.12"\nsdk_min = "0.4.0"\n', encoding='utf-8')
    (path / 'inputs.json').write_text('{"name": "Minha empresa"}\n', encoding='utf-8')
    (path / 'requirements.txt').write_text('# Adicione aqui apenas as bibliotecas usadas pelo seu bot.\n', encoding='utf-8')
    (path / '.gitignore').write_text('.venv/\n__pycache__/\nresult.json\n.env\n*.local.json\ndist/\n', encoding='utf-8')
    (path / 'README.md').write_text('# Framework Nexus\n\nCom o SDK local instalado:\n\n```sh\nnexus run --inputs inputs.json\n```\n\nEdite `bot.py` para implementar sua automação. O SDK ainda não foi publicado no PyPI.\nInstale-o a partir de `apps/sdk` do repositório Nexus.\n', encoding='utf-8')
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nexus', description='Crie e teste seus bots Nexus localmente.')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Criar um projeto em uma nova pasta')
    init.add_argument('directory', type=Path)
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
    package.add_argument('--output', type=Path, help='Caminho explícito; padrão: <projeto>/dist/<nome>.zip')
    validate = sub.add_parser('validate', help='Validar configuração sem executar o código do bot')
    validate.add_argument('--project', type=Path, default=Path('.'))
    validate.add_argument('--strict', action='store_true', help='Tratar avisos como erros')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            init_project(args.directory)
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
        else:
            root = args.project.resolve()
            validate_project(root, check_environment=False)
            script = entrypoint(args.project)
            with (root / 'nexus.toml').open('rb') as stream:
                config = tomllib.load(stream)
            name = config.get('name', root.name)
            if (not isinstance(name, str) or not name or name.startswith('.') or name.endswith(('.', ' '))
                    or any(character in name for character in '/\\:*?"<>|')
                    or any(ord(character) < 32 for character in name)):
                raise RobotError('Nome do projeto inválido para um arquivo ZIP.')
            output = (args.output or root / 'dist' / f'{name}.zip').resolve()
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
            print(json.dumps({'file': str(output), 'entrypoint': script.relative_to(root).as_posix(),
                              'checksum': hashlib.sha256(output.read_bytes()).hexdigest(),
                              'size_bytes': output.stat().st_size}, indent=2))
        return 0
    except (OSError, ValueError, RobotError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
