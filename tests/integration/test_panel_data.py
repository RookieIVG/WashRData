"""Writes what the panel gets from a real Home Assistant, for the browser test.

tests/frontend/panel_smoke.mjs loads the panel with this data, so the panel
is tested against the real WebSocket format, not a hand-written copy.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_dumps
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.washrdata.const import CONF_DEVICE_TYPE, CONF_POWER_SENSOR, DOMAIN
from custom_components.washrdata.websocket import device_json
from tests.engine.sim import vary
from tests.engine.test_matching import COTTON, PROGRAMS, QUICK, trained
from tests.engine.test_split import MERGED

from .conftest import POWER
from .test_programs import _play

OUT = Path(__file__).parents[1] / "frontend" / "overview.json"
PRICE = "input_number.strompreis_energie"


async def test_write_panel_data(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Waschmaschine",
        unique_id=POWER,
        data={"name": "Waschmaschine", CONF_DEVICE_TYPE: "washer", CONF_POWER_SENSOR: POWER},
        options={"price_entity": PRICE},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(PRICE, "32", {"unit_of_measurement": "ct/kWh"})
    hass.states.async_set(POWER, "1.5", {"unit_of_measurement": "W"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = entry.runtime_data
    bench = trained(COTTON, QUICK, runs=3)
    history = bench.tracker.history
    for i, c in enumerate(history):
        c.cost = round(c.energy_wh / 1000 * 0.32, 3)
        if i < 2:  # stored before 0.2.4: evaluated later
            c.recognised, c.known_programs = None, []
    # Two washes merged into one, to be split.
    last = history[-1]
    start = last.end + 3600
    history.append(replace(MERGED, start=start, end=start + MERGED.duration()))
    device.tracker.programs = list(bench.tracker.programs)
    device.tracker.history = history
    device.schedule_evaluation()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # After the evaluation, so its recommendation stays: one run recognised right
    # but too unsure (○), and a first run of a new program recognised as another (✗).
    unsure = next(c for c in history if c.program == QUICK and not c.recognition_retro)
    unsure.match_confidence = 0.3
    fresh = replace(unsure, start=history[-1].start + 7200, end=history[-1].start + 7200 + unsure.duration())
    fresh.program, fresh.match_confidence, fresh.recognition_retro = "Feinwäsche", 0.8, False
    device.tracker.programs.append("Feinwäsche")
    history.append(fresh)

    # A cycle running for 30 minutes.
    await _play(hass, freezer, [(300, 1.5), *vary(PROGRAMS[COTTON], 9)[:60]])

    # What washrdata/overview sends; called directly because the WebSocket
    # session would expire with the clock moved by hours.
    overview = json.loads(json_dumps({"devices": [device_json(device)]}))
    overview_device = overview["devices"][0]
    assert overview_device["current"] is not None
    assert overview_device["evaluation"]["cycles"] == 6
    OUT.write_text(json.dumps(overview, indent=1))
