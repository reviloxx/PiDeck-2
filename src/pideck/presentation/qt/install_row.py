"""Readable status text for catalog applications being installed or removed."""

from pideck.application.ports.installation import InstallProgress, InstallStage

from .localization import tr


def install_status_text(language: str, progress: InstallProgress) -> str:
    """Describe one progress snapshot in the selected language."""
    if progress.stage is InstallStage.DOWNLOADING and progress.percent is not None:
        if progress.operations and progress.operations > 1:
            return tr(
                language, "install_downloading_step",
                step=progress.operation, steps=progress.operations, percent=progress.percent,
            )
        return tr(language, "install_downloading", percent=progress.percent)
    keys = {
        InstallStage.PREPARING: "install_preparing",
        InstallStage.INSTALLING_FLATPAK: "install_installing_flatpak",
        InstallStage.DOWNLOADING: "install_starting_download",
        InstallStage.INSTALLING: "install_installing",
        InstallStage.REMOVING: "install_removing",
        InstallStage.FINISHING: "install_finishing",
    }
    return tr(language, keys[progress.stage])
