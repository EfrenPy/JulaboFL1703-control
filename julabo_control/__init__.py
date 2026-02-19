"""High level helpers for controlling Julabo chillers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("julabo-control")
except PackageNotFoundError:
    __version__ = "0.0.0"

from .alarm import TemperatureAlarm
from .core import (
    DEFAULT_BAUDRATE,
    DEFAULT_TIMEOUT,
    PORT_CACHE_PATH,
    SETPOINT_MAX,
    SETPOINT_MIN,
    ChillerBackend,
    JulaboChiller,
    JulaboError,
    SerialSettings,
    auto_detect_port,
    candidate_ports,
    forget_port,
    probe_port,
    read_cached_port,
    remember_port,
)
from .notifications import send_desktop_notification
from .remote_client import RemoteChillerClient
from .schedule import ScheduleRunner, ScheduleStep, SetpointSchedule
from .simulator import FakeChillerBackend
from .temperature_logger import TemperatureFileLogger

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "run_gui": (".gui", "run_gui"),
    "BaseChillerApp": (".ui", "BaseChillerApp"),
}


def __getattr__(name: str) -> object:
    entry = _LAZY_IMPORTS.get(name)
    if entry is not None:
        module_path, attr = entry
        import importlib

        mod = importlib.import_module(module_path, __name__)
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    names = list(globals().keys())
    names.extend(k for k in _LAZY_IMPORTS if k not in names)
    return names


__all__ = [
    "__version__",
    "DEFAULT_BAUDRATE",
    "DEFAULT_TIMEOUT",
    "PORT_CACHE_PATH",
    "SETPOINT_MAX",
    "SETPOINT_MIN",
    "JulaboChiller",
    "JulaboError",
    "ScheduleRunner",
    "ScheduleStep",
    "SerialSettings",
    "SetpointSchedule",
    "TemperatureAlarm",
    "TemperatureFileLogger",
    "auto_detect_port",
    "candidate_ports",
    "forget_port",
    "probe_port",
    "read_cached_port",
    "remember_port",
    "run_gui",
    "send_desktop_notification",
    "BaseChillerApp",
    "ChillerBackend",
    "FakeChillerBackend",
    "RemoteChillerClient",
]
