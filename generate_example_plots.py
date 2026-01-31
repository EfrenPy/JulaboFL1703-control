"""Generate example plots for the README using simulated chiller data."""

from __future__ import annotations

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "docs", "images")
os.makedirs(OUTPUT_DIR, exist_ok=True)

STYLE = {
    "figure.facecolor": "white",
    "axes.facecolor": "#fafafa",
    "axes.grid": True,
    "grid.linestyle": "--",
    "grid.linewidth": 0.5,
    "grid.alpha": 0.7,
    "font.size": 11,
}


def _save(fig: plt.Figure, name: str) -> None:
    path = os.path.join(OUTPUT_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {path}")


def plot_setpoint_tracking() -> None:
    """Simulate a chiller cooling down to a setpoint and stabilising."""

    np.random.seed(42)
    dt = 5  # seconds between readings
    total_seconds = 30 * 60  # 30 minutes
    n = total_seconds // dt
    time_min = np.arange(n) * dt / 60

    setpoint = 15.0
    initial_temp = 22.0
    tau = 5.0  # time-constant in minutes

    temp = setpoint + (initial_temp - setpoint) * np.exp(-time_min / tau)
    temp += np.random.normal(0, 0.08, size=n)

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot(time_min, temp, color="#1f77b4", linewidth=1.4, label="Process temperature")
        ax.axhline(setpoint, color="#d62728", linewidth=1.2, linestyle="--", label=f"Setpoint ({setpoint} °C)")
        ax.set_xlabel("Time (min)")
        ax.set_ylabel("Temperature (°C)")
        ax.set_title("Cooling to setpoint")
        ax.legend(loc="upper right", fontsize=10)
        ax.set_xlim(0, time_min[-1])
        fig.tight_layout()
        _save(fig, "setpoint_tracking.png")


def plot_temperature_monitoring() -> None:
    """Simulate steady-state temperature monitoring around a setpoint."""

    np.random.seed(7)
    dt = 5
    total_seconds = 20 * 60
    n = total_seconds // dt
    time_min = np.arange(n) * dt / 60

    setpoint = 18.0
    temp = setpoint + 0.15 * np.sin(2 * np.pi * time_min / 8) + np.random.normal(0, 0.06, size=n)

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot(time_min, temp, marker="o", markersize=1.5, linewidth=1.0, color="#1f77b4", label="Process temperature")
        ax.axhline(setpoint, color="#d62728", linewidth=1.2, linestyle="--", label=f"Setpoint ({setpoint} °C)")
        ax.fill_between(time_min, setpoint - 0.3, setpoint + 0.3, color="#d62728", alpha=0.07, label="±0.3 °C band")
        ax.set_xlabel("Time (min)")
        ax.set_ylabel("Temperature (°C)")
        ax.set_title("Steady-state temperature monitoring")
        ax.legend(loc="upper right", fontsize=10)
        ax.set_xlim(0, time_min[-1])
        fig.tight_layout()
        _save(fig, "temperature_monitoring.png")


def plot_setpoint_steps() -> None:
    """Simulate stepping through multiple setpoints."""

    np.random.seed(99)
    dt = 5
    total_seconds = 60 * 60  # 1 hour
    n = total_seconds // dt
    time_min = np.arange(n) * dt / 60

    # Step sequence: 20 -> 15 -> 10 -> 18
    setpoints = [(0, 20.0), (15, 15.0), (30, 10.0), (45, 18.0)]
    sp_line = np.empty(n)
    current_sp = setpoints[0][1]
    sp_idx = 0
    for i, t in enumerate(time_min):
        if sp_idx + 1 < len(setpoints) and t >= setpoints[sp_idx + 1][0]:
            sp_idx += 1
            current_sp = setpoints[sp_idx][1]
        sp_line[i] = current_sp

    # Simulate temperature response
    tau = 4.0
    temp = np.empty(n)
    temp[0] = setpoints[0][1]
    for i in range(1, n):
        dt_min = (time_min[i] - time_min[i - 1])
        alpha = 1 - np.exp(-dt_min / tau)
        temp[i] = temp[i - 1] + alpha * (sp_line[i] - temp[i - 1])
    temp += np.random.normal(0, 0.1, size=n)

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot(time_min, temp, color="#1f77b4", linewidth=1.4, label="Process temperature")
        ax.step(time_min, sp_line, color="#d62728", linewidth=1.2, linestyle="--", where="post", label="Setpoint")
        ax.set_xlabel("Time (min)")
        ax.set_ylabel("Temperature (°C)")
        ax.set_title("Multi-step setpoint programme")
        ax.legend(loc="upper right", fontsize=10)
        ax.set_xlim(0, time_min[-1])
        fig.tight_layout()
        _save(fig, "setpoint_steps.png")


if __name__ == "__main__":
    print("Generating example plots...")
    plot_setpoint_tracking()
    plot_temperature_monitoring()
    plot_setpoint_steps()
    print("Done.")
