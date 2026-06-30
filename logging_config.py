import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once.

    Prevents duplicate `basicConfig()` calls across modules.
    """

    root = logging.getLogger()
    if getattr(root, "_configured_by_blackboxai", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    setattr(root, "_configured_by_blackboxai", True)

