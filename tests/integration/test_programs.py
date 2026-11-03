"""Programs in Home Assistant: label, recognise, services, diagnostics."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.json import json_dumps
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.washrdata.const import DOMAIN
from custom_components.washrdata.diagnostics import async_get_config_entry_diagnostics
from tests.engine.sim import vary
from tests.engine.test_matching import COTTON, PROGRAMS, QUICK

from .conftest import POWER

W = {"unit_of_measurement": "W"}


async def _play(hass: HomeAssistant, freezer: FrozenDateTimeFactory, curve) -> None:
    """One plug report per 30 s chunk, ticks in between."""
    for duration, watts in curve:
        hass.states.async_set(POWER, f"{watts:.1f}", W)
        await hass.async_block_till_done()
        for _ in range(max(1, round(duration / 30))):
            freezer.tick(timedelta(seconds=30))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()


async def _run(hass, freezer, program: str, seed: int) -> None:
    await _play(hass, freezer, [(300, 1.5), *vary(PROGRAMS[program], seed), (2400, 1.5)])


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    hass.states.async_set(POWER, "1.5", W)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    return device.id


async def _select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.washer_program", "option": option}, blocking=True
    )


async def test_label_with_select_and_recognise(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    device_id = await _setup(hass, washer_entry)
    select = hass.states.get("select.washer_program")
    assert select.attributes["options"] == ["none"]

    for name in (COTTON, QUICK):
        await hass.services.async_call(
            DOMAIN, "create_program", {"device_id": device_id, "program": name}, blocking=True
        )
    assert hass.states.get("select.washer_program").attributes["options"] == ["none", COTTON, QUICK]

    for seed in (1, 2):
        await _run(hass, freezer, COTTON, seed)
        await _select(hass, COTTON)
        await _run(hass, freezer, QUICK, seed)
        await _select(hass, QUICK)
    assert hass.states.get("select.washer_program").state == QUICK

    curve = vary(PROGRAMS[COTTON], 3)
    await _play(hass, freezer, [(300, 1.5), *curve[:60]])  # 30 min in
    assert hass.states.get("sensor.washer_program").state == COTTON
    assert hass.states.get("sensor.washer_program").attributes["confidence"] >= 50
    assert hass.states.get("select.washer_program").state == COTTON
    remaining = float(hass.states.get("sensor.washer_time_remaining").state)
    assert 30 <= remaining <= 55
    assert 30 <= float(hass.states.get("sensor.washer_progress").state) <= 60
    assert hass.states.get("sensor.washer_expected_end").state not in ("unknown", "unavailable")

    await _play(hass, freezer, [*curve[60:], (2400, 1.5)])
    last = hass.states.get("sensor.washer_last_cycle_duration").attributes
    assert last["program"] == COTTON
    assert last["program_source"] == "auto"
    assert hass.states.get("sensor.washer_program").state == "unknown"


async def test_label_cycle_service_and_errors(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    device_id = await _setup(hass, washer_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "label_cycle", {"device_id": device_id, "program": "X"}, blocking=True
        )

    await _run(hass, freezer, QUICK, 1)
    await hass.services.async_call(
        DOMAIN, "label_cycle", {"device_id": device_id, "program": "Kurz"}, blocking=True
    )
    assert hass.states.get("select.washer_program").state == "Kurz"

    await hass.services.async_call(
        DOMAIN,
        "rename_program",
        {"device_id": device_id, "program": "Kurz", "new_name": "Kurz 30"},
        blocking=True,
    )
    assert hass.states.get("select.washer_program").state == "Kurz 30"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "delete_program", {"device_id": device_id, "program": "Gibt es nicht"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "create_program", {"device_id": "falsch", "program": "X"}, blocking=True
        )

    await hass.services.async_call(
        DOMAIN, "delete_program", {"device_id": device_id, "program": "Kurz 30"}, blocking=True
    )
    assert hass.states.get("select.washer_program").state == "none"
    assert hass.states.get("select.washer_program").attributes["options"] == ["none"]


async def test_labels_survive_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    device_id = await _setup(hass, washer_entry)
    await _run(hass, freezer, QUICK, 1)
    await hass.services.async_call(
        DOMAIN, "label_cycle", {"device_id": device_id, "program": QUICK}, blocking=True
    )
    assert await hass.config_entries.async_reload(washer_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.washer_program").state == QUICK
    assert washer_entry.runtime_data.tracker.programs == [QUICK]


async def test_diagnostics_contain_traces(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    await _setup(hass, washer_entry)
    await _run(hass, freezer, QUICK, 1)
    # Called directly: the frozen clock has expired the test client's token.
    diag = await async_get_config_entry_diagnostics(hass, washer_entry)
    json_dumps(diag)
    assert diag["entry"]["data"]["power_sensor"] == POWER
    cycles = diag["tracker"]["cycles"]
    assert len(cycles) == 1
    assert len(cycles[0]["trace"]) > 10
    assert diag["config"]["end_delay_s"] == 300


async def test_store_of_version_0_1_loads(hass: HomeAssistant, hass_storage, washer_entry) -> None:
    """0.1 stored only detector and cycles; programs are new."""
    hass_storage[f"{DOMAIN}.{washer_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{washer_entry.entry_id}",
        "data": {
            "detector": {"state": "idle", "cycle_count": 3, "total_energy_wh": 4200.0},
            "cycles": [{"start": 1000.0, "end": 4600.0, "energy_wh": 900.0, "trace": [[0, 500.0]]}],
        },
    }
    await _setup(hass, washer_entry)
    assert hass.states.get("sensor.washer_cycle_count").state == "3"
    assert hass.states.get("sensor.washer_last_cycle_duration").state == "60.0"
    assert hass.states.get("select.washer_program").attributes["options"] == ["none"]


async def _type_program(hass: HomeAssistant, name: str) -> None:
    await hass.services.async_call(
        "text", "set_value", {"entity_id": "text.washer_new_program", "value": name}, blocking=True
    )


async def test_new_program_text_labels_last_cycle(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    await _setup(hass, washer_entry)
    assert hass.states.get("text.washer_new_program").state == ""

    # Without any cycle the program is only created.
    await _type_program(hass, "Pflegeleicht")
    assert hass.states.get("select.washer_program").attributes["options"] == ["none", "Pflegeleicht"]

    await _run(hass, freezer, QUICK, 1)
    await _type_program(hass, "  Kurz 30  ")
    assert hass.states.get("select.washer_program").state == "Kurz 30"
    assert hass.states.get("text.washer_new_program").state == ""
    tracker = washer_entry.runtime_data.tracker
    assert tracker.programs == ["Pflegeleicht", "Kurz 30"]
    assert tracker.last_cycle.program_source == "manual"

    # Blank input changes nothing.
    await _type_program(hass, "   ")
    assert tracker.programs == ["Pflegeleicht", "Kurz 30"]


async def test_new_program_text_labels_running_cycle(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    await _setup(hass, washer_entry)
    curve = vary(PROGRAMS[QUICK], 4)
    await _play(hass, freezer, [(300, 1.5), *curve[:20]])
    assert hass.states.get("sensor.washer_state").state == "running"
    await _type_program(hass, "Kurz 30")
    assert hass.states.get("sensor.washer_program").state == "Kurz 30"
    await _play(hass, freezer, [*curve[20:], (2400, 1.5)])
    last = hass.states.get("sensor.washer_last_cycle_duration").attributes
    assert last["program"] == "Kurz 30"
    assert last["program_source"] == "manual"


async def test_phase_sensor_follows_the_wash(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, washer_entry: MockConfigEntry
) -> None:
    """The simulated cotton program: fill, heat, wash, drain, spin. The cycle is
    confirmed only during the heating (the fill draws too little), so the
    sensor starts there."""
    await _setup(hass, washer_entry)
    assert hass.states.get("sensor.washer_phase").state == "unknown"
    curve = PROGRAMS[COTTON]
    seen = []
    for duration, watts in [(300, 1.5), *curve]:
        hass.states.async_set(POWER, f"{watts:.1f}", W)
        await hass.async_block_till_done()
        for _ in range(max(1, round(duration / 30))):
            freezer.tick(timedelta(seconds=30))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            state = hass.states.get("sensor.washer_phase").state
            if state not in ("unknown", *seen[-1:]):
                seen.append(state)
    assert seen[:2] == ["heating", "washing"]
    assert seen[-1] == "spinning"
    assert hass.states.get("sensor.washer_phase").attributes["options"][0] == "filling"
