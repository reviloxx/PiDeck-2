"""Helpers for tests requiring an active top-level Qt event loop."""

from functools import wraps

from PySide6.QtCore import QTimer


def with_qt_event_loop(function):
    """Run a test inside the application's event loop and propagate failures."""
    @wraps(function)
    def run(application, *args, **kwargs):
        failures = []
        completed = []

        def execute():
            try:
                function(application, *args, **kwargs)
            except BaseException as error:
                failures.append(error)
            finally:
                completed.append(True)
                application.quit()

        QTimer.singleShot(0, execute)
        application.exec()
        assert completed, "Qt event loop exited before the test completed"
        if failures:
            raise failures[0]

    return run
