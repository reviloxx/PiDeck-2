"""Errors raised by domain and boundary validation."""


class PiDeckError(Exception):
    """Base class for expected PiDeck errors."""


class ConfigurationError(PiDeckError):
    """Base class for configuration loading and persistence errors."""


class ConfigurationValidationError(ConfigurationError):
    """Raised when configuration data violates the expected schema."""


class ThemeError(PiDeckError):
    """Base class for theme loading and validation errors."""


class ThemeValidationError(ThemeError):
    """Raised when a theme definition or its assets are invalid."""
