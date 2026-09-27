"""Nexus bot authoring API. No network connection is required for local runs."""
from importlib.metadata import PackageNotFoundError, version as _package_version
from pathlib import Path
import re

def _sdk_version() -> str:
    try:
        return _package_version("nexus-sdk")
    except PackageNotFoundError:
        # Source checkouts can run the CLI before installation. Read the same
        # pyproject metadata used by the build instead of maintaining a second version.
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        match = re.search(r'(?m)^version\\s*=\\s*"([0-9]+\\.[0-9]+\\.[0-9]+)"', pyproject.read_text(encoding="utf-8"))
        if not match:
            raise RuntimeError("Não foi possível determinar a versão do nexus-sdk")
        return match.group(1)

__version__ = _sdk_version()

from .core import Automation, Context, RobotError, robot
from .errors import BusinessError, ConfigurationError, ValidationError, TransientError
from .models import Model

__all__ = ['Automation', 'Context', 'RobotError', 'robot', 'Model', 'BusinessError',
           'ConfigurationError', 'ValidationError', 'TransientError']
