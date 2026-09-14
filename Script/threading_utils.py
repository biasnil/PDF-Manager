import threading
from typing import Callable


def run_in_background(root, fn: Callable, on_done: Callable, on_error: Callable) -> None:
    """
    Run fn() on a worker thread. Once it finishes, on_done(result) or
    on_error(exception) is scheduled back onto the main thread via
    root.after — never called directly from the worker thread.
    """

    def target():
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - surface any failure to the user
            # `e` is deleted by Python the moment this except block ends
            # (standard behavior, to avoid keeping traceback objects
            # alive via a reference cycle) — but root.after only
            # SCHEDULES the lambda below to run later, once Tk's event
            # loop gets to it, by which point `e` is already gone from
            # this scope. Capture it into a plain local first so the
            # lambda closes over THAT instead — err isn't subject to
            # the except-clause auto-delete.
            err = e
            root.after(0, lambda: on_error(err))
        else:
            root.after(0, lambda: on_done(result))

    threading.Thread(target=target, daemon=True).start()


def threadsafe_progress(root, progress_row) -> Callable:
    """
    Wrap a ProgressRow's set_progress so it's safe to pass as a `progress`
    callback into a background-threaded PDF operation: every update is
    marshaled onto the main thread via root.after instead of touching Tk
    widgets directly from the worker thread.
    """

    def cb(current: int, total: int, message: str) -> None:
        root.after(0, progress_row.set_progress, current, total, message)

    return cb