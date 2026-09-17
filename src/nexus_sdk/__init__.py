"""Nexus bot authoring API. No network connection is required for local runs."""
from .core import Automation, Context, RobotError, robot
from .errors import BusinessError, ConfigurationError, ValidationError, TransientError
from .models import Model

__all__ = ['Automation', 'Context', 'RobotError', 'robot', 'Model', 'BusinessError',
           'ConfigurationError', 'ValidationError', 'TransientError']
__version__ = '0.4.15'
