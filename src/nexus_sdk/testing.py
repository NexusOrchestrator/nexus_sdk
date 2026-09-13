"""In-memory bot tests. Never contacts the API or reads process credentials."""
from dataclasses import dataclass
from copy import deepcopy
from .core import Context
from .queues import Queues


@dataclass
class RunResult:
    result: dict
    publications: list[dict]


def run_robot(bot, *, inputs=None, credentials=None, queues=None):
    ctx = Context(inputs=deepcopy(inputs or {}), secrets=deepcopy(credentials or {}),
                  queues=Queues(queues), execution_id='test', _write_result=False)
    return RunResult(bot(ctx), ctx.queues.publications)
