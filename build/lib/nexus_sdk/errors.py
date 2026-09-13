class RobotError(Exception):
    """Expected failure. Messages must not contain secrets."""
    code = 'ROBOT_ERROR'


class BusinessError(RobotError):
    code = 'BUSINESS_ERROR'


class ConfigurationError(RobotError):
    code = 'CONFIGURATION_ERROR'


class ValidationError(BusinessError):
    code = 'VALIDATION_ERROR'


class TransientError(RobotError):
    code = 'TRANSIENT_ERROR'
