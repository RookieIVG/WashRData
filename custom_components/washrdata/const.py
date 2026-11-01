"""Constants of the WashRData integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.const import Platform

from .engine import PRESETS, DetectorConfig, DeviceType

DOMAIN = "washrdata"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SELECT, Platform.SENSOR, Platform.TEXT]

CONF_DEVICE_TYPE = "device_type"
CONF_POWER_SENSOR = "power_sensor"
# Optional energy meter of the plug (kWh); energy then comes from it.
CONF_ENERGY_SENSOR = "energy_sensor"

# Option of the program select that means "no program".
NO_PROGRAM = "none"

# Options, shown in the options flow. End delay is entered in minutes.
OPT_START_POWER = "start_power_w"
OPT_STOP_POWER = "stop_power_w"
OPT_END_DELAY_MIN = "end_delay_min"
OPT_ANTI_CREASE = "anti_crease"
# Activity this soon after an end continues the same cycle; 0 never does.
OPT_RESUME_WINDOW_MIN = "resume_window_min"
# Upper limit for the learned end delay.
OPT_MAX_END_DELAY_MIN = "max_end_delay_min"
# Recognition thresholds in percent.
OPT_SHOW_CONFIDENCE = "show_confidence"
OPT_AUTO_LABEL_CONFIDENCE = "auto_label_confidence"
# Entity with the energy price per kWh (input_number, number or sensor).
OPT_PRICE_ENTITY = "price_entity"

# Leave-one-out evaluation runs this long after the last change.
EVALUATION_DELAY_S = 30
STORAGE_VERSION = 1
SAVE_DELAY_S = 30
# Lets timeouts fire when the plug only reports changes.
TICK_INTERVAL = timedelta(seconds=30)


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


def build_config(device_type: DeviceType, options: dict[str, Any]) -> DetectorConfig:
    """Device type preset with the user's options applied."""
    preset = PRESETS[device_type]
    overrides: dict[str, Any] = {}
    if OPT_START_POWER in options:
        overrides["start_power_w"] = float(options[OPT_START_POWER])
    if OPT_STOP_POWER in options:
        overrides["stop_power_w"] = float(options[OPT_STOP_POWER])
    end_delay = float(options.get(OPT_END_DELAY_MIN, preset.end_delay_s / 60)) * 60
    overrides["end_delay_s"] = end_delay
    max_end_delay = float(options.get(OPT_MAX_END_DELAY_MIN, preset.max_end_delay_s / 60)) * 60
    overrides["max_end_delay_s"] = max(max_end_delay, end_delay)
    if OPT_RESUME_WINDOW_MIN in options:
        overrides["resume_window_s"] = float(options[OPT_RESUME_WINDOW_MIN]) * 60
    if OPT_ANTI_CREASE in options:
        overrides["anti_crease"] = bool(options[OPT_ANTI_CREASE])
    if OPT_SHOW_CONFIDENCE in options:
        overrides["show_confidence"] = float(options[OPT_SHOW_CONFIDENCE]) / 100
    if OPT_AUTO_LABEL_CONFIDENCE in options:
        overrides["auto_label_confidence"] = float(options[OPT_AUTO_LABEL_CONFIDENCE]) / 100
    return preset.with_overrides(overrides)
