import multiprocessing
import os, sys, logging, traceback


def _setup_logging():
    from app import core
    log_path = os.path.join(core.data_dir(), "app.log")
    # Windowed PyInstaller builds have sys.stdout/stderr = None; redirect them to the log file
    if sys.stdout is None or sys.stderr is None:
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None: sys.stdout = f
        if sys.stderr is None: sys.stderr = f
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(processName)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )

    def _hook(t, v, tb):
        logging.getLogger("app").critical("Uncaught exception:\n%s", "".join(traceback.format_exception(t, v, tb)))
    sys.excepthook = _hook
    return log_path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _setup_logging()
    logging.getLogger("app").info("starting pid=%s argv=%s", os.getpid(), sys.argv)
    try:
        from app.main import run
        run()
    except SystemExit:
        raise
    except BaseException:
        logging.getLogger("app").exception("fatal startup error")
        raise
