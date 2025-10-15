# Julabo-control

Python module and command line utility for remote controlling a Julabo
recirculating chiller via RS232.

## Requirements

* Python 3.9+
* [`pyserial`](https://pyserial.readthedocs.io/en/latest/) (`pip install pyserial`)
* Null-modem cable and RS232-to-USB converter connected to the chiller

## Preparing the chiller

1. Switch the Julabo chiller off.
2. Wait at least five seconds.
3. Press **arrow up** and **return** simultaneously and, while holding them,
   press the power button.
4. The display must show `IOn`. If it shows `IOFF`, repeat the previous
   steps.
5. After a few seconds the display switches to `rOFF`, indicating that
   remote control is active.

## Running the CLI

The tool can automatically probe USB serial adapters until it finds the
Julabo controller. Simply run one of the following commands:

```bash
python -m julabo_control version
python -m julabo_control get-setpoint
python -m julabo_control set-setpoint 18.5
python -m julabo_control start
```

You can still pass `--port /dev/ttyUSB0` (or another path) to override the
auto-detected device. The last working port is cached in
`~/.julabo_control_port` so subsequent runs, including the GUI, connect
instantly without additional prompts.

The most common commands are implemented directly:

* `version` – return the identification string
* `status` – return the status message (manual section 11.4)
* `get-setpoint` – read the current setpoint (`in_sp_00`)
* `set-setpoint` – update the setpoint (`out_sp_00 xxx.x`)
* `get-temperature` – read the process temperature (`in_pv_00`)
* `start` / `stop` – toggle remote cooling (`out_mode_05 1`/`0`)
* `send` – send an arbitrary raw command (for advanced usage)
* `gui` – launch a desktop interface for monitoring and changing the setpoint

The serial configuration matches the recommendation from the manual and
the tested `screen` configuration: 4800 baud, 7 data bits, even parity,
1 stop bit, RTS/CTS flow control and a 2 second read timeout.

## Python API

```python
from julabo_control import JulaboChiller, SerialSettings

with JulaboChiller(SerialSettings(port="/dev/ttyUSB0")) as chiller:
    print(chiller.identify())
    print(chiller.get_temperature())
    chiller.set_setpoint(18.5)
    chiller.start()
```

If the chiller returns an error message (see manual section 11.5) the
library raises `JulaboError`. Timeout and serial errors are also
surfaced, making it easy to handle communication issues in higher level
scripts.

## Graphical interface

The GUI command provides a lightweight Tk window that continuously
displays the current setpoint and process temperature, refreshing every
five seconds. Enter a new setpoint in the input field and press **Apply**
to send the update to the chiller.

Launch the interface with `python -m julabo_control gui`. The program will
reuse the last working port or search connected adapters until it reaches
the chiller. If auto-detection fails the window still opens and shows the
connection error so you can troubleshoot cabling or power without re-running
the command. Once connected it continuously refreshes the display every five
seconds.
