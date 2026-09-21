"""Static preflight: validates configuration without importing or running bot code."""
import ast
from pathlib import Path
import platform
import re
import sys
import tomllib
from .errors import ConfigurationError


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
        raise ConfigurationError('Versões devem usar major.minor ou major.minor.patch.')
    return tuple(int(part) for part in value.split('.'))


def validate_project(project, *, check_environment=True, credentials=None, queues=None):
    root = Path(project).resolve()
    with (root / 'nexus.toml').open('rb') as stream:
        config = tomllib.load(stream)
    value = config.get('entrypoint', 'bot.py')
    if not isinstance(value, str):
        raise ConfigurationError('Entrypoint inválido.')
    script = (root / value).resolve()
    if not script.is_relative_to(root) or not script.is_file() or script.suffix != '.py':
        raise ConfigurationError('Entrypoint deve ser um arquivo Python dentro do projeto.')
    try:
        tree = ast.parse(script.read_text(encoding='utf-8'), filename=value)
    except (SyntaxError, UnicodeError) as error:
        raise ConfigurationError('Erro de sintaxe/codificação no entrypoint.') from error
    # Only top-level declarations; inspecting a package never executes its imports.
    bots = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
            and any(isinstance(decorator, ast.Name) and decorator.id == 'robot'
                    or isinstance(decorator, ast.Attribute) and decorator.attr == 'robot'
                    for decorator in node.decorator_list)]
    if len(bots) != 1:
        raise ConfigurationError('Declare apenas uma função ou classe @robot no entrypoint.')
    if isinstance(bots[0], ast.AsyncFunctionDef):
        raise ConfigurationError('O framework suporta bots síncronos.')
    runtime = config.get('runtime', {})
    if not isinstance(runtime, dict):
        raise ConfigurationError('runtime deve ser uma tabela TOML.')
    python = runtime.get('python')
    sdk_min = runtime.get('sdk_min')
    systems = runtime.get('platforms', [])
    if not isinstance(systems, list) or any(system not in ('Linux', 'Windows', 'Darwin') for system in systems):
        raise ConfigurationError('platforms aceita Linux, Windows e Darwin.')
    python_version = version(python) if python else None
    sdk_version = version(sdk_min) if sdk_min else None
    if check_environment:
        from . import __version__
        if python_version and tuple(sys.version_info[:len(python_version)]) != python_version:
            raise ConfigurationError(f'Projeto requer Python {python}.')
        if sdk_version and version(__version__) < sdk_version:
            raise ConfigurationError(f'Projeto requer framework {sdk_min} ou superior.')
        if systems and platform.system() not in systems:
            raise ConfigurationError('Sistema operacional incompatível com o projeto.')
    required = config.get('credentials', [])
    if not isinstance(required, list) or len(required) > 50 or any(not isinstance(name, str) or not name for name in required):
        raise ConfigurationError('credentials deve ser uma lista de nomes de credenciais.')
    if credentials is not None:
        missing = set(required) - set(credentials)
        if missing:
            raise ConfigurationError('Credenciais obrigatórias não vinculadas: ' + ', '.join(sorted(missing)))
    expected_queues = config.get('queues', {})
    if not isinstance(expected_queues, dict) or set(expected_queues) - {'input', 'output'}:
        raise ConfigurationError('queues aceita apenas input e output.')
    for direction, name in expected_queues.items():
        if not isinstance(name, str) or not name:
            raise ConfigurationError('Declare filas pelo nome.')
        if queues is not None and (queues.get(direction) or {}).get('name') != name:
            raise ConfigurationError(f'Fila {direction} não corresponde ao vínculo requerido: {name}')
    warnings = []
    requirements = root / 'requirements.txt'
    visited = set()
    def check_requirements(path):
        path = path.resolve()
        if path in visited:
            return
        if not path.is_relative_to(root) or not path.is_file():
            raise ConfigurationError('Requirements deve apontar para arquivos dentro do projeto.')
        visited.add(path)
        if len(visited) > 50 or path.stat().st_size > 32768:
            raise ConfigurationError('Requirements excede os limites de validação.')
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r '):
                check_requirements(path.parent / line[3:].strip())
            elif re.match(r'^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_, -]+\])?\s*@\s*https://github\.com/.+/archive/refs/tags/[^/\s]+\.zip(?:\s*#.*)?$', line):
                continue
            elif '==' not in line or line.startswith(('-', '.', '/')):
                warnings.append('Dependência sem versão exata ou opção especial; revisão necessária em requirements.txt.')
    if requirements.exists():
        check_requirements(requirements)
    if not runtime:
        warnings.append('Projeto não declara compatibilidade em [runtime].')
    return {'name': config.get('name', root.name), 'entrypoint': script.relative_to(root).as_posix(),
            'runtime': runtime, 'credentials': required, 'queues': expected_queues,
            'warnings': list(dict.fromkeys(warnings))}
