"""Strict dataclass contracts, without runtime dependency on a validation package."""
from dataclasses import MISSING, asdict, fields, is_dataclass
import types
from typing import Union, Literal, get_args, get_origin, get_type_hints
from .errors import ConfigurationError, ValidationError


def convert(value, annotation, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        for candidate in args:
            try:
                return convert(value, candidate, path)
            except ValidationError:
                pass
    elif origin is Literal:
        if any(type(value) is type(option) and value == option for option in args):
            return value
    elif origin is list and isinstance(value, list):
        return [convert(item, args[0], path + '[]') for item in value]
    elif origin is dict and isinstance(value, dict):
        if args[0] is not str:
            raise ConfigurationError('Modelos JSON exigem chaves string.')
        return {convert(key, str, path): convert(item, args[1], path + '.*') for key, item in value.items()}
    elif isinstance(annotation, type) and issubclass(annotation, Model):
        return annotation.parse(value, path=path)
    elif annotation in (str, int, bool, float, type(None), dict, list):
        if type(value) is annotation or annotation is float and type(value) is int:
            return value
    elif origin not in (Union, types.UnionType, Literal, list, dict):
        raise ConfigurationError(f'Tipo não suportado no contrato: {path}')
    raise ValidationError(f'Tipo ou valor inválido no campo: {path}')


class Model:
    """Subclass with @dataclass. No implicit coercion and no unknown fields."""
    @classmethod
    def parse(cls, data, *, path='inputs'):
        if not is_dataclass(cls):
            raise ConfigurationError('Declare modelos com @dataclass.')
        if isinstance(data, cls):
            data = asdict(data)
        if not isinstance(data, dict):
            raise ValidationError(f'{path} deve ser um objeto.')
        definitions = {field.name: field for field in fields(cls)}
        if set(data) - set(definitions):
            raise ValidationError(f'{path} contém campos não declarados.')
        hints = get_type_hints(cls)
        values = {}
        for name, field in definitions.items():
            if name in data:
                value = data[name]
            elif field.default is not MISSING:
                value = field.default
            elif field.default_factory is not MISSING:
                value = field.default_factory()
            else:
                raise ValidationError(f'Campo obrigatório ausente: {path}.{name}')
            values[name] = convert(value, hints[name], f'{path}.{name}')
        return cls(**values)

    def to_dict(self):
        return asdict(self)
