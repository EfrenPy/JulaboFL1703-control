# Julabo Control Suite

Comprehensive utilities for operating a Julabo recirculating chiller from Python.  The
project bundles a reusable library, a command-line interface, a local desktop GUI, and a
small TCP service with a remote GUI client.

## Project layout

```
Julabo-control/
├── julabo_control/        # Reusable Python package
│   ├── core.py            # Serial helpers and JulaboChiller implementation
│   ├── gui.py             # Local Tk interface built on top of the core module
│   ├── ui.py              # Shared Tk/matplotlib utilities
│   ├── cli.py             # Command line entrypoint (``python -m julabo_control``)
│   └── __main__.py        # Thin wrapper that dispatches to ``cli.main``
├── remote_client.py       # Tk GUI that talks to a remote Julabo server
├── remote_control_server.py  # TCP JSON server exposing Julabo commands
├── requirements.txt       # Runtime dependencies (PySerial + matplotlib)
└── README.md              # This guide
```

The code is structured so that all shared logic (serial communication, GUI helpers, font
configuration, temperature chart handling, and port caching) lives inside the
`julabo_control` package.  The standalone scripts import those helpers, keeping the
entrypoints concise and easy to maintain.

## Requirements

* Python 3.8+
* [`pyserial`](https://pyserial.readthedocs.io/en/latest/)
* [`matplotlib`](https://matplotlib.org/)
* A Julabo chiller connected through a null-modem cable and an RS232-to-USB adapter

Install the dependencies into a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Preparing the chiller

1. Switch the Julabo chiller off.
2. Wait at least five seconds.
3. Press **arrow up** and **return** simultaneously and, while holding them, press the
   power button.
4. The display must show `IOn`. If it shows `IOFF`, repeat the previous steps.
5. After a few seconds the display switches to `rOFF`, indicating that remote control is
   active.

## Local tools

### Command line interface

The CLI automatically probes USB serial adapters until it finds the Julabo controller.
This works on Linux (``/dev/ttyUSB0`` style paths) and on Windows (``COM3`` style names).
Run any of the following commands:

```bash
python -m julabo_control version
python -m julabo_control get-setpoint
python -m julabo_control set-setpoint 18.5
python -m julabo_control start
```

Optional flags:

* `--port /dev/ttyUSB0` – override the auto-detected device (use `COM3` on Windows)
* `--timeout 5.0` – change the serial read timeout in seconds

The CLI caches the last working port in `~/.julabo_control_port`, so subsequent runs (and
GUI launches) reuse the stored device automatically.

Supported subcommands:

* `version` – return the identification string
* `status` – return the status message (manual section 11.4)
* `get-setpoint` – read the current setpoint (`in_sp_00`)
* `set-setpoint` – update the setpoint (`out_sp_00 xxx.x`)
* `get-temperature` – read the process temperature (`in_pv_00`)
* `start` / `stop` – toggle remote cooling (`out_mode_05 1`/`0`)
* `send` – send an arbitrary raw command (for advanced usage)
* `gui` – launch the desktop interface described below

### Local GUI

`julabo_control.gui.run_gui` powers a lightweight Tk window that displays the current
setpoint and process temperature, refreshing every five seconds.  Enter a new setpoint in
the input field and press **Apply** to send the update to the chiller.  A matplotlib chart
keeps track of the most recent readings.

Launch the interface with:

```bash
python -m julabo_control gui
```

If no port is specified the program reuses the cached port or searches connected
adapters until it finds the chiller.

### Python API

```python
from julabo_control import JulaboChiller, SerialSettings

with JulaboChiller(SerialSettings(port="/dev/ttyUSB0")) as chiller:
    print(chiller.identify())
    print(chiller.get_temperature())
    chiller.set_setpoint(18.5)
    chiller.start()
```

The API raises `JulaboError` when the chiller reports an error message.  Timeouts and
PySerial exceptions are surfaced directly, allowing higher-level code to handle
communication issues gracefully.

## Remote operation

For situations where the computer connected to the Julabo does not have local user
access, the suite provides a TCP JSON server and a companion GUI client.

### Server

```bash
python remote_control_server.py --host 0.0.0.0 --port 8765
```

* Automatically scans local serial ports, covering both `/dev/ttyUSB*` and `COM*`
  device names.
* Retries the search every few seconds until the chiller becomes available.
* Accepts optional overrides such as `--baudrate`, `--timeout`, `--host`, and `--port`.

### Client

```bash
python remote_client.py --host pctpx4ctl.cern.ch --port 8765
```

The client defaults to `pctpx4ctl.cern.ch` when no host is provided.  Use `--host` to
point it to another server.  The window mirrors the local GUI: it shows the current
status, temperature, setpoint, and machine state, with controls for refreshing readings,
updating the setpoint, and starting or stopping circulation.  The embedded temperature
chart reuses the shared plotting helper from the core package for a consistent look.

Each client request opens a short-lived TCP connection, making it easy to operate the
chiller from multiple machines on the same network without managing persistent sessions.

## Development tips

* Run `python -m julabo_control --help` to inspect CLI options.
* `python remote_control_server.py --help` and `python remote_client.py --help` describe
  optional flags for the network utilities.
* The shared helpers live under `julabo_control/ui.py` and can be reused by additional
  tools if you extend the suite.
