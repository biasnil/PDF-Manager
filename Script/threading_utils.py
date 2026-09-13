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
            root.after(0, lambda: on_error(e))
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
