# Troubleshooting

Common issues and solutions when running Julabo Control Suite.

## Serial port not detected

**Symptom:** `auto_detect_port` raises an error or the CLI prints
"No Julabo adapter found."

**Solutions:**

1. Check the cable is plugged in and the chiller is powered on.
2. Verify the device appears in `/dev/`:
   ```bash
   ls /dev/ttyUSB* /dev/ttyACM*
   ```
3. Ensure your user has permission to access the port:
   ```bash
   sudo usermod -aG dialout $USER
   # Log out and back in for the change to take effect
   ```
4. On Linux, add a udev rule for persistent naming:
   ```
   # /etc/udev/rules.d/99-julabo.rules
   SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="julabo"
   ```
   Reload with `sudo udevadm control --reload-rules && sudo udevadm trigger`.

## Connection timeout

**Symptom:** `TimeoutError` when sending commands.

**Solutions:**

- Confirm the baudrate matches the chiller setting (default 4800).
- Try a longer timeout: `julabo --timeout 3.0 status`.
- Check the cable supports RS232 signalling (not just USB-TTL).
- Ensure RTS/CTS flow control is not blocked by the adapter.

## "Serial connection lost" in server logs

The watchdog thread detects serial failures and attempts to reconnect
automatically. If reconnection fails repeatedly it will stop trying after
20 attempts and log a `CRITICAL` message.

**Solutions:**

- Check the physical cable connection.
- Restart the server once the cable is reattached.
- Review logs for the specific `OSError` or `SerialException` message.

## Rate limiting

**Symptom:** Clients receive `"Rate limit exceeded"` errors.

This means the server was started with `--rate-limit N` and a single IP
sent more than *N* requests in one minute.

**Solutions:**

- Increase the limit: `--rate-limit 120`.
- Set to 0 to disable: `--rate-limit 0`.
- Distribute requests across multiple clients.

## TLS handshake failures

**Symptom:** Clients fail to connect when `--tls-cert` / `--tls-key` are set.

**Solutions:**

- Ensure the client uses `ssl=True` or wraps the socket in an SSL context.
- Verify the certificate is valid and not expired:
  ```bash
  openssl x509 -in server.crt -noout -dates
  ```
- If using self-signed certs, the client must either disable verification or
  trust the CA certificate.

## Web dashboard not loading

**Symptom:** Browser shows connection refused on the web dashboard port.

**Solutions:**

- The web dashboard is a separate entry point. Make sure you are using
  `julabo-web` or the `web` module, not the plain TCP server.
- Check the `--host` and `--port` flags.
- Verify no firewall is blocking the port.

## GUI fails to start

**Symptom:** `ImportError` for tkinter or matplotlib.

**Solutions:**

- Install tkinter: `sudo apt install python3-tk` (Debian/Ubuntu).
- Install matplotlib: `pip install matplotlib`.
- On headless servers, use the TCP server + remote client instead of the GUI.
