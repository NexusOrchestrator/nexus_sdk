"""Local credential storage for the Nexus CLI (`nexus login`).

Credentials are stored as named profiles (one per API + account combination,
e.g. "local", "staging-clienteA", "prod-clienteB") since a token is tied to a
single account on a single deployment. Switching profiles only needs
`nexus profile-use <name>` instead of logging in again. `NEXUS_PROFILE`
overrides the active profile per-shell without touching the persisted
default, which is handy for one-off commands.
"""
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlparse

CREDENTIALS_PATH = Path(os.environ.get('NEXUS_CREDENTIALS_FILE', str(Path.home() / '.nexus' / 'credentials.json')))
DEFAULT_PROFILE = 'default'


def _write(data: dict):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')
    # Tokens are long-lived secrets; restrict the file to the current user only.
    try:
        os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _read() -> dict:
    if not CREDENTIALS_PATH.exists():
        return {'active': DEFAULT_PROFILE, 'profiles': {}}
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'active': DEFAULT_PROFILE, 'profiles': {}}
    # Migrate the old single-profile format (flat dict with a "token" key) transparently.
    if 'profiles' not in data:
        return {'active': DEFAULT_PROFILE, 'profiles': {DEFAULT_PROFILE: data} if data.get('token') else {}}
    return data


def profile_name_for(api_base_url: str) -> str:
    host = urlparse(api_base_url).hostname or api_base_url
    return host.replace('.', '-')


def save_credentials(api_base_url: str, token: str, organization_id: str, organization_name: str, profile: str = None):
    data = _read()
    name = profile or profile_name_for(api_base_url)
    data['profiles'][name] = {
        'api_base_url': api_base_url.rstrip('/'),
        'token': token,
        'organization_id': organization_id,
        'organization_name': organization_name,
    }
    data['active'] = name
    _write(data)
    return name


def load_credentials():
    data = _read()
    name = os.environ.get('NEXUS_PROFILE') or data.get('active')
    return data['profiles'].get(name)


def list_profiles():
    data = _read()
    active = os.environ.get('NEXUS_PROFILE') or data.get('active')
    return active, data['profiles']


def use_profile(name: str):
    data = _read()
    if name not in data['profiles']:
        raise KeyError(name)
    data['active'] = name
    _write(data)


def set_default_environment(key: str):
    data = _read()
    name = os.environ.get('NEXUS_PROFILE') or data.get('active')
    if name not in data['profiles']:
        return
    data['profiles'][name]['default_environment'] = key
    _write(data)


def clear_credentials(profile: str = None):
    data = _read()
    if profile:
        data['profiles'].pop(profile, None)
        if data.get('active') == profile:
            data['active'] = next(iter(data['profiles']), DEFAULT_PROFILE)
        _write(data)
        return
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
