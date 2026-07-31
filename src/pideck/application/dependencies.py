"""Runtime dependencies assembled by the composition root."""

from dataclasses import dataclass
import logging

from pideck.application.ports.configuration import ConfigurationRepository
from pideck.application.ports.process import ProcessSupervisor
from pideck.application.ports.theme import ThemeRepository
from pideck.domain.configuration import Configuration


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    """Hold injected repositories and the validated runtime configuration."""

    configuration: Configuration
    configuration_repository: ConfigurationRepository
    theme_repository: ThemeRepository
    process_supervisor: ProcessSupervisor
    logger: logging.Logger
