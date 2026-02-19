# Deployment Guide

This guide covers running Julabo Control Suite in production environments.

## Docker

### Build the image

```bash
docker build -t julabo-control .
```

### Run with serial device access

```bash
docker run -d \
  --name julabo-server \
  --device /dev/ttyUSB0:/dev/ttyUSB0 \
  -p 8765:8765 \
  julabo-control \
  --host 0.0.0.0
```

### Environment variables

| Variable | Purpose |
|----------|---------|
| `JULABO_AUTH_TOKEN` | Authentication token (fallback if `--auth-token` not set) |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin password in the compose stack |

### Docker Compose

The included `docker-compose.yml` starts the server, Prometheus, and Grafana:

```bash
docker compose up -d
```

Services:

| Service | Port | Purpose |
|---------|------|---------|
| `julabo-server` | 8765 | TCP command server |
| `julabo-server` | 9100 | Prometheus metrics |
| `prometheus` | 9090 | Metrics collection |
| `grafana` | 3000 | Dashboards |

Edit `docker-compose.yml` to change the serial device path (`/dev/ttyUSB0`) or
add TLS flags.

## systemd

Create `/etc/systemd/system/julabo-server.service`:

```ini
[Unit]
Description=Julabo FL1703 TCP Server
After=network.target

[Service]
Type=simple
User=julabo
ExecStart=/usr/local/bin/julabo-server \
    /dev/ttyUSB0 \
    --host 0.0.0.0 \
    --auth-token-file /etc/julabo/token \
    --metrics-port 9100
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now julabo-server
```

## Monitoring with Prometheus

Start the server with `--metrics-port 9100` to expose a `/metrics` endpoint.

Add a scrape target in `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: julabo
    static_configs:
      - targets: ['julabo-server:9100']
```

Available metrics include `julabo_temperature_celsius`,
`julabo_setpoint_celsius`, `julabo_is_running`, `julabo_commands_total`,
and `julabo_commands_errors_total`.

Import the Grafana dashboard from `monitoring/grafana/dashboards/` or create
panels using these metrics.
