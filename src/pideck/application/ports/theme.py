"""Theme repository ports."""

from typing import Protocol

from pideck.domain.theme import ThemeDefinition


class ThemeRepository(Protocol):
    """Resolve validated themes by identifier."""

    def get(self, identifier: str) -> ThemeDefinition:
        """Return a theme or raise a theme error."""
        ...
