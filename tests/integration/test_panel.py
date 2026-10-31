"""Sidebar panel and its WebSocket commands, against a real Home Assistant."""

from __future__ import annotations

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator, WebSocketGenerator

from custom_components.washrdata.engine import CycleRecord, LabelSource

from .conftest import POWER


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[CycleRecord, CycleRecord]:
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tracker = entry.runtime_data.tracker
    tracker.programs = ["Cotton 40", "Quick"]
    trace = [(0.0, 15.0), (120.0, 2000.0), (1320.0, 250.0), (3600.0, 450.0), (4080.0, 2.0)]
    hit = CycleRecord(
        start=1_700_000_000,
        end=1_700_004_200,
        energy_wh=950,
        peak_power_w=2000,
        program="Cotton 40",
        program_source=LabelSource.AUTO,
        match_confidence=0.82,
        recognised="Cotton 40",
        known_programs=["Cotton 40", "Quick"],
        trace=trace,
    )
    miss = CycleRecord(
        start=1_700_100_000,
        end=1_700_104_200,
        energy_wh=900,
        peak_power_w=2000,
        program="Quick",
        program_source=LabelSource.MANUAL,
        match_confidence=0.7,
        recognised="Cotton 40",
        known_programs=["Cotton 40", "Quick"],
        trace=trace,
    )
    tracker.history = [hit, miss]
    return hit, miss


async def test_panel_is_registered_and_served(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, washer_entry: MockConfigEntry
) -> None:
    await _setup(hass, washer_entry)
    panel = hass.data[DATA_PANELS]["washrdata"]
    assert panel.require_admin is False
    url = panel.config["_panel_custom"]["module_url"]
    assert url.startswith("/washrdata_static/washrdata-panel.js?v=")

    client = await hass_client()
    response = await client.get(url)
    assert response.status == 200
    assert 'customElements.define("washrdata-panel"' in await response.text()


async def test_overview_shows_cycles_and_accuracy(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, washer_entry: MockConfigEntry
) -> None:
    _hit, miss = await _setup(hass, washer_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "washrdata/overview"})
    result = (await ws.receive_json())["result"]

    device = result["devices"][0]
    assert device["title"] == "Washer"
    assert device["programs"] == ["Cotton 40", "Quick"]
    assert device["accuracy"] == {
        "cycles": 2,
        "hits": 1,
        "unsure": 0,
        "programs": {
            "Cotton 40": {"cycles": 1, "hits": 1, "unsure": 0},
            "Quick": {"cycles": 1, "hits": 0, "unsure": 0},
        },
    }
    newest = device["cycles"][0]
    assert newest["id"] == miss.id
    assert newest["recognised"] == "Cotton 40"
    assert newest["program_source"] == "manual"
    assert newest["reference"] is True
    # One value per minute: 4200 s
    assert len(newest["curve"]) == 70
    assert newest["curve"][5] == 2000.0

    washer_entry.runtime_data.changed()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.washer_recognition_accuracy")
    assert state.state == "50"
    assert state.attributes["programs"]["Quick"] == {"cycles": 1, "hits": 0, "unsure": 0, "accuracy": 0}
    assert hass.states.get("sensor.washer_recognition_confidence").state == "unknown"


async def test_label_rename_delete_from_panel(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, washer_entry: MockConfigEntry
) -> None:
    hit, miss = await _setup(hass, washer_entry)
    tracker = washer_entry.runtime_data.tracker
    ws = await hass_ws_client(hass)
    entry_id = washer_entry.entry_id

    await ws.send_json_auto_id({"type": "washrdata/subscribe"})
    assert (await ws.receive_json())["success"]

    await ws.send_json_auto_id(
        {"type": "washrdata/label", "entry_id": entry_id, "cycle_id": miss.id, "program": "Delicates"}
    )
    # The change is announced to the subscription, then the command answers.
    messages = [await ws.receive_json(), await ws.receive_json()]
    assert {m["type"] for m in messages} == {"event", "result"}
    assert miss.program == "Delicates" and "Delicates" in tracker.programs

    await ws.send_json_auto_id(
        {"type": "washrdata/label", "entry_id": entry_id, "cycle_id": miss.id, "program": None}
    )
    await ws.receive_json()
    await ws.receive_json()
    assert miss.program is None

    await ws.send_json_auto_id(
        {
            "type": "washrdata/rename_program",
            "entry_id": entry_id,
            "program": "Cotton 40",
            "new_name": "Cotton",
        }
    )
    await ws.receive_json()
    await ws.receive_json()
    assert hit.program == "Cotton" and hit.recognised == "Cotton"

    await ws.send_json_auto_id(
        {"type": "washrdata/delete_program", "entry_id": entry_id, "program": "Cotton"}
    )
    await ws.receive_json()
    await ws.receive_json()
    assert hit.program is None

    await ws.send_json_auto_id(
        {"type": "washrdata/label", "entry_id": entry_id, "cycle_id": "nope", "program": "X"}
    )
    response = await ws.receive_json()
    assert response["error"]["code"] == "not_found"

    await ws.send_json_auto_id(
        {"type": "washrdata/label", "entry_id": "nope", "cycle_id": hit.id, "program": "X"}
    )
    response = await ws.receive_json()
    assert response["error"]["code"] == "not_found"
