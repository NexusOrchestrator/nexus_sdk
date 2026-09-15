"""Local credential storage for the Nexus CLI (`nexus login`)."""
import json
import os
import stat
from pathlib import Path

CREDENTIALS_PATH = Path(os.environ.get('NEXUS_CREDENTIALS_FILE', str(Path.home() / '.nexus' / 'credentials.json')))


def save_credentials(api_base_url: str, token: str, organization_id: str, organization_name: str):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps({
        'api_base_url': api_base_url.rstrip('/'),
        'token': token,
        'organization_id': organization_id,
        'organization_name': organization_name,
    }, indent=2), encoding='utf-8')
    # Token is a long-lived secret; restrict to the current user only.
    try:
        os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_credentials():
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        return json.loads(CREDENTIALS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def clear_credentials():
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
