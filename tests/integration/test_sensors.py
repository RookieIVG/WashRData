"""Changing the sensors of an appliance, and energy from the plug's meter."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.washrdata.const import CONF_DEVICE_TYPE, CONF_ENERGY_SENSOR, CONF_POWER_SENSOR, DOMAIN
from custom_components.washrdata.engine import CycleRecord
from custom_components.washrdata.meter import EnergyMeter, parse_energy
from tests.engine.sim import vary
from tests.engine.test_matching import PROGRAMS, QUICK

from .conftest import POWER

NEW_POWER = "sensor.new_plug_power"
METER = "sensor.plug_energy"
W = {"unit_of_measurement": "W"}
KWH = {"unit_of_measurement": "kWh"}


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.states.async_set(POWER, "1.5", W)
    hass.states.async_set(NEW_POWER, "1.5", W)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_reconfigure_keeps_history(hass: HomeAssistant, washer_entry: MockConfigEntry) -> None:
    await _setup(hass, washer_entry)
    tracker = washer_entry.runtime_data.tracker
    tracker.programs = ["Quick"]
    tracker.history = [
        CycleRecord(start=1_700_000_000, end=1_700_001_800, program="Quick", trace=[(0.0, 250.0)])
    ]

    result = await washer_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POWER_SENSOR: NEW_POWER, CONF_ENERGY_SENSOR: METER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert washer_entry.unique_id == NEW_POWER
    assert washer_entry.data[CONF_POWER_SENSOR] == NEW_POWER
    assert washer_entry.data[CONF_ENERGY_SENSOR] == METER
    device = washer_entry.runtime_data
    assert device.power_entity == NEW_POWER
    assert [c.program for c in device.tracker.history] == ["Quick"]
    assert device.tracker.programs == ["Quick"]

    # The new sensor drives detection, the old one no longer does.
    hass.states.async_set(POWER, "2000", W)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washer_state").state == "idle"
    hass.states.async_set(NEW_POWER, "2000", W)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washer_state").state == "starting"

    # Clearing the meter removes it.
    result = await washer_entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_POWER_SENSOR: NEW_POWER})
    await hass.async_block_till_done()
    assert CONF_ENERGY_SENSOR not in washer_entry.data


async def test_reconfigure_rejects_a_sensor_in_use(
    hass: HomeAssistant, washer_entry: MockConfigEntry
) -> None:
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Dryer",
        unique_id=NEW_POWER,
        data={"name": "Dryer", CONF_DEVICE_TYPE: "dryer", CONF_POWER_SENSOR: NEW_POWER},
    )
    other.add_to_hass(hass)
    await _setup(hass, washer_entry)
    result = await washer_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_POWER_SENSOR: NEW_POWER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert washer_entry.data[CONF_POWER_SENSOR] == POWER


async def test_setup_with_energy_meter(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Washer", "device_type": "washer", CONF_POWER_SENSOR: POWER, CONF_ENERGY_SENSOR: METER},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENERGY_SENSOR] == METER


@pytest.mark.parametrize("factor", [1.3, 0.75])
async def test_cycle_energy_from_meter(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, factor: float
) -> None:
    """The meter reads `factor` times what integrating the power gives; the cycle takes the meter."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Washer",
        unique_id=POWER,
        data={
            "name": "Washer",
            CONF_DEVICE_TYPE: "washer",
            CONF_POWER_SENSOR: POWER,
            CONF_ENERGY_SENSOR: METER,
        },
        options={"price_entity": "input_number.price"},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("input_number.price", "0.30", {"unit_of_measurement": "EUR/kWh"})
    hass.states.async_set(METER, "100.000", KWH)
    await _setup(hass, entry)
    device = entry.runtime_data
    base = 100_000.0
    counted = 0.0  # what the meter has counted, in Wh

    for duration, watts in [(300, 1.5), *vary(PROGRAMS[QUICK], 1), (2400, 1.5)]:
        hass.states.async_set(POWER, f"{watts:.1f}", W)
        await hass.async_block_till_done()
        for _ in range(max(1, round(duration / 30))):
            freezer.tick(timedelta(seconds=30))
            counted += watts * 30 / 3600 * factor
            # A meter that steps in 10 Wh, as many plugs do.
            hass.states.async_set(METER, f"{(base + counted // 10 * 10) / 1000:.3f}", KWH)
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

    cycle = device.last_cycle
    assert cycle is not None
    integrated = sum(w * d for d, w in vary(PROGRAMS[QUICK], 1)) / 3600
    assert cycle.energy_wh == pytest.approx(integrated * factor, abs=15)
    assert abs(cycle.energy_wh - integrated) > 30  # the meter, not the integration
    assert cycle.cost == pytest.approx(cycle.energy_wh / 1000 * 0.30, rel=0.02)
    last = hass.states.get("sensor.washer_last_cycle_energy")
    assert float(last.state) == pytest.approx(cycle.energy_wh / 1000, abs=0.002)


def test_meter_counts_through_a_reset_and_needs_coverage() -> None:
    meter = EnergyMeter()
    meter.add(100.0, 5000.0)
    meter.add(200.0, 5100.0)
    meter.add(300.0, 20.0)  # the plug restarted its count
    meter.add(400.0, 70.0)
    meter.add(450.0, None)  # unavailable: ignored
    assert meter.energy(150.0, 400.0) == pytest.approx(5100.0 + 70.0 - 5000.0)
    assert meter.energy(50.0, 400.0) is None  # no reading before the start


def test_parse_energy_units() -> None:
    assert parse_energy(State(METER, "1.5", {"unit_of_measurement": "kWh"})) == 1500.0
    assert parse_energy(State(METER, "1500", {"unit_of_measurement": "Wh"})) == 1500.0
    assert parse_energy(State(METER, "unavailable")) is None
    assert parse_energy(State(METER, "x")) is None
