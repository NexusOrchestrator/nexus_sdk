"""Minimal HTTP client for the Nexus API using only the standard library."""
import json
import mimetypes
import uuid
import urllib.error
import urllib.request
from .errors import RobotError


def api_request(base_url, method, path, token=None, payload=None, headers=None, timeout=30):
    url = f'{base_url.rstrip("/")}{path}'
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request_headers = {'Content-Type': 'application/json'}
    if token:
        request_headers['Authorization'] = f'Bearer {token}'
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(detail).get('detail', detail)
        except ValueError:
            pass
        raise RobotError(f'Falha na requisição ({error.code}): {detail}') from error
    except urllib.error.URLError as error:
        raise RobotError(f'Não foi possível conectar em {base_url}: {error.reason}') from error


def api_upload(base_url, path, token, fields, file_field, file_name, file_bytes, timeout=120):
    """Multipart/form-data POST built with only the standard library."""
    boundary = uuid.uuid4().hex
    body = bytearray()

    def add_field(name, value):
        body.extend(f'--{boundary}\r\n'.encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f'{value}\r\n'.encode())

    for name, value in fields.items():
        if value is not None:
            add_field(name, value)
    content_type = mimetypes.guess_type(file_name)[0] or 'application/octet-stream'
    body.extend(f'--{boundary}\r\n'.encode())
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode())
    body.extend(f'Content-Type: {content_type}\r\n\r\n'.encode())
    body.extend(file_bytes)
    body.extend(b'\r\n')
    body.extend(f'--{boundary}--\r\n'.encode())

    url = f'{base_url.rstrip("/")}{path}'
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Authorization': f'Bearer {token}',
    }
    request = urllib.request.Request(url, data=bytes(body), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
            return json.loads(content) if content else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(detail).get('detail', detail)
        except ValueError:
            pass
        raise RobotError(f'Falha no upload ({error.code}): {detail}') from error
    except urllib.error.URLError as error:
        raise RobotError(f'Não foi possível conectar em {base_url}: {error.reason}') from error

