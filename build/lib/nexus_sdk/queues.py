"""Execution-scoped queue delivery; publications are committed by the orchestrator."""
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from .errors import ConfigurationError, ValidationError

MAX_PUBLICATION_BYTES = 256 * 1024
MAX_PAYLOAD_BYTES = 30 * 1024


@dataclass(frozen=True)
class Message:
    id: str
    payload: dict
    queue_id: str
    queue_name: str
    attempts: int = 1

    def parse(self, model):
        return model.parse(self.payload, path='message')


class Queues:
    def __init__(self, context=None):
        self.context = deepcopy(context or {})
        self._consumed = False
        self._publications = {}

    def consume(self, queue=None):
        source = self.context.get('input')
        if queue and (not source or queue not in {source['id'], source['name']}):
            raise ConfigurationError('Fila de entrada não autorizada nesta execução.')
        message = self.context.get('message')
        if not source or not message or self._consumed:
            return None
        self._consumed = True
        return Message(id=message['id'], payload=deepcopy(message['payload']), queue_id=source['id'],
                       queue_name=source['name'], attempts=message.get('attempts', 1))

    def publish(self, payload, *, key, queue=None):
        output = self.context.get('output')
        if not output or queue and queue not in {output['id'], output['name']}:
            raise ConfigurationError('Fila de saída não autorizada nesta execução.')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', key):
            raise ValidationError('Informe uma chave de publicação estável com até 100 caracteres.')
        from .core import encode_object
        if len(encode_object(payload)) > MAX_PAYLOAD_BYTES:
            raise ValidationError('Mensagem de fila excede 30 KB.')
        item = {'queue_id': output['id'], 'key': key, 'payload': deepcopy(payload)}
        if key in self._publications and self._publications[key] != item:
            raise ValidationError('Chave de publicação reutilizada com outro conteúdo.')
        proposed = {**self._publications, key: item}
        if len(proposed) > 100 or len(json.dumps(list(proposed.values()), allow_nan=False).encode()) > MAX_PUBLICATION_BYTES:
            raise ValidationError('Publicações excedem 100 mensagens ou 256 KB por execução.')
        self._publications = proposed
        return key

    @property
    def publications(self):
        return deepcopy(list(self._publications.values()))
