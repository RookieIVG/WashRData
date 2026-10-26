"""Stored dryer cycles that still contain anti-crease turns are trimmed on start."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washrdata.const import CONF_DEVICE_TYPE, CONF_POWER_SENSOR, DOMAIN
from custom_components.washrdata.engine import PRESETS, DeviceType, Tracker
from tests.engine.test_bursts import WITH_TURNS

from .conftest import POWER

MIN = 60.0


async def test_stored_cycles_trimmed_on_start(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dryer",
        unique_id=POWER,
        data={"name": "Dryer", CONF_DEVICE_TYPE: "dryer", CONF_POWER_SENSOR: POWER},
    )
    entry.add_to_hass(hass)
    cycle, end_min = WITH_TURNS[0]
    stored = replace(cycle, start=1_700_000_000.0, end=1_700_000_000.0 + cycle.duration())
    tracker = Tracker(PRESETS[DeviceType.DRYER], history=[stored])
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": tracker.to_dict(),
    }

    hass.states.async_set(POWER, "1", {"unit_of_measurement": "W"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    trimmed = entry.runtime_data.tracker.history[0]
    assert trimmed.duration() == pytest.approx(end_min * MIN, abs=1.5 * MIN)
    saved = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["cycles"][0]
    assert saved["end"] == pytest.approx(trimmed.end)
