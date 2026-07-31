"""Pure launcher navigation and tile selection use cases."""

from dataclasses import dataclass
from enum import StrEnum

from pideck.domain.configuration import ApplicationDefinition, Configuration


class NavigationDirection(StrEnum):
    """Directions accepted from keyboard and future input adapters."""

    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class LauncherState:
    """Immutable snapshot of the current application-tile selection."""

    applications: tuple[ApplicationDefinition, ...]
    focused_index: int = 0
    columns: int = 1

    @property
    def focused_application(self) -> ApplicationDefinition | None:
        """Return the focused application, if the launcher has any tiles."""
        if not self.applications:
            return None
        return self.applications[self.focused_index]


class LauncherController:
    """Manage visible applications and deterministic grid navigation."""

    def __init__(self, configuration: Configuration) -> None:
        """Create a controller from the validated home-screen configuration."""
        applications_by_id = {
            application.identifier: application
            for application in configuration.applications
        }
        visible_ids = configuration.home.visible_applications
        if visible_ids:
            applications = tuple(
                applications_by_id[identifier]
                for identifier in visible_ids
                if identifier in applications_by_id
            )
        else:
            applications = tuple(configuration.applications)
        self._state = LauncherState(applications=applications)

    @property
    def state(self) -> LauncherState:
        """Return the current immutable launcher state."""
        return self._state

    def set_columns(self, columns: int) -> LauncherState:
        """Update the grid width while preserving the focused application."""
        if columns <= 0:
            raise ValueError("Launcher grid must have at least one column")
        self._state = LauncherState(
            applications=self._state.applications,
            focused_index=self._state.focused_index,
            columns=columns,
        )
        return self._state

    def move(self, direction: NavigationDirection) -> LauncherState:
        """Move focus within the grid and clamp at its edges."""
        if not self._state.applications:
            return self._state
        index = self._state.focused_index
        columns = self._state.columns
        row, column = divmod(index, columns)
        if direction is NavigationDirection.LEFT and column > 0:
            index -= 1
        elif direction is NavigationDirection.RIGHT and index + 1 < len(self._state.applications):
            index += 1
        elif direction is NavigationDirection.UP and row > 0:
            index -= columns
        elif direction is NavigationDirection.DOWN:
            candidate = index + columns
            if candidate < len(self._state.applications):
                index = candidate
        self._state = LauncherState(
            applications=self._state.applications,
            focused_index=index,
            columns=columns,
        )
        return self._state

    def activate(self) -> ApplicationDefinition | None:
        """Return the focused application as an activation intent."""
        return self._state.focused_application
