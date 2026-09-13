"""Shared runtime for the local CLI and the remote agent subprocess."""
import argparse
import logging
import os
from pathlib import Path
import runpy
import sys
import traceback
from .core import Robot, RobotError, Context, SecretFilter, write_object, secret_values
from .project import validate_project


def execute_script(script, *, allow_legacy=False, context=None):
    namespace = runpy.run_path(str(script), run_name='__main__')
    robots = {id(value): value for value in namespace.values()
              if isinstance(value, Robot) and value.__module__ == '__main__'}
    if len(robots) > 1:
        raise RobotError('Defina apenas uma função @robot por arquivo de entrada.')
    if not robots:
        if allow_legacy:
            return None
        raise RobotError('Nenhuma função @robot encontrada no arquivo de entrada.')
    bot = next(iter(robots.values()))
    return bot.result if bot.executed else bot(context)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--allow-legacy', action='store_true')
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args(argv)
    if args.project_root:
        sys.path.insert(0, str(args.project_root.resolve()))
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s', stream=sys.stderr)
    context = None
    try:
        context = Context.from_environment()
        for handler in logging.getLogger().handlers:
            handler.addFilter(SecretFilter(context.secrets))
        if args.project_root and (args.project_root / 'nexus.toml').exists():
            validate_project(args.project_root, credentials=context.secrets, queues=context.queues.context)
        logging.getLogger('nexus.runtime').info('Execução iniciada')
        execute_script(args.script, allow_legacy=args.allow_legacy, context=context)
        logging.getLogger('nexus.runtime').info('Execução concluída')
        return 0
    except Exception as error:
        # Do not send arbitrary exception strings/tracebacks into central results.
        code = error.code if isinstance(error, RobotError) else 'UNEXPECTED_ERROR'
        message = str(error)[:1000] if isinstance(error, RobotError) else f'Falha não tratada no bot ({type(error).__name__}).'
        for secret in sorted(secret_values(context.secrets if context else {}), key=len, reverse=True):
            message = message.replace(secret, '***')
        target = os.environ.get('NEXUS_ERROR_FILE')
        if target:
            write_object(target, {'code': code, 'message': message})
        print(f'{code}: {message}', file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
