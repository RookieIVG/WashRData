"""WebSocket commands of the WashRData panel.

Any logged-in user may read, label and split, like the program select entity.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OPT_AUTO_LABEL_CONFIDENCE, OPT_SHOW_CONFIDENCE, signal_update
from .device import WashRDataDevice
from .engine import CycleRecord, MatchResult
from .engine.evaluation import CycleEvaluation, Evaluation
from .engine.matching import resample
from .engine.split import quiet_stretches

# Quiet stretches offered as split points per cycle.
SPLIT_POINTS = 3
# Curves go to the panel as one mean value per minute.
CURVE_STEP_S = 60.0


def _curve(cycle: CycleRecord, now: float | None = None) -> list[float]:
    return [round(w, 1) for w in resample(cycle.trace, cycle.duration(now), CURVE_STEP_S)]


def _cycle_evaluation(e: CycleEvaluation | None, show: float) -> dict[str, Any] | None:
    if e is None:
        return None
    return {
        "final": e.final,
        "confidence": e.final_confidence,
        "new_program": e.new_program,
        "first_shown_min": e.first_shown(show),
        "wrong_shown": e.wrong_shown(show),
        "remaining_error_s": {str(p): e.remaining_error(p) for p in (0.25, 0.5, 0.75)},
    }


def _evaluation_json(evaluation: Evaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "cycles": len(evaluation.cycles),
        "recommended_show": evaluation.recommended_show,
        "recommended_auto": evaluation.recommended_auto,
        "note": evaluation.note,
        "new_program_cycles": evaluation.new_program_cycles,
        "thresholds": [vars(r) for r in evaluation.thresholds],
        "remaining": [vars(r) for r in evaluation.remaining],
    }


def cycle_json(
    cycle: CycleRecord,
    references: set[str],
    now: float | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": cycle.id,
        "start": cycle.start,
        "end": cycle.end,
        "duration_s": round(cycle.duration(now)),
        "energy_kwh": round(cycle.energy_wh / 1000, 3),
        "peak_w": round(cycle.peak_power_w),
        "interrupted": cycle.interrupted,
        "program": cycle.program,
        "program_source": cycle.program_source,
        "recognised": cycle.recognised,
        "confidence": cycle.match_confidence,
        "known_programs": cycle.known_programs,
        "recognition_retro": cycle.recognition_retro,
        "cost": round(cycle.cost, 2) if cycle.cost is not None else None,
        "evaluation": evaluation,
        "reference": cycle.id in references,
        "curve": _curve(cycle, now),
    }


def _match_json(match: MatchResult | None) -> dict[str, Any] | None:
    if match is None:
        return None
    return {
        "program": match.program,
        "confidence": match.confidence,
        "manual": match.manual,
        "recognised": match.recognised,
        "recognition_confidence": match.recognition_confidence,
        "remaining_s": match.remaining_s,
        "progress": match.progress,
        "candidates": [{"program": p, "score": s} for p, s in match.candidates],
    }


def revision(device: WashRDataDevice) -> str:
    """Changes whenever the stored cycles, programs or the evaluation change."""
    tracker = device.tracker
    key = (
        tuple(tracker.programs),
        tuple((c.id, c.program, c.program_source, c.recognised, c.match_confidence) for c in tracker.history),
        id(device.evaluation),
    )
    return str(hash(key))


def live_json(device: WashRDataDevice) -> dict[str, Any]:
    """The running cycle only, cheap enough to send every few seconds."""
    tracker = device.tracker
    now = dt_util.utcnow().timestamp()
    refs = {c.id for cycles in tracker.references().values() for c in cycles}
    return {
        "entry_id": device.entry.entry_id,
        "state": tracker.state.value,
        "current": cycle_json(tracker.cycle, refs, now) if tracker.cycle else None,
        "match": _match_json(tracker.match),
        "revision": revision(device),
    }


def device_json(device: WashRDataDevice) -> dict[str, Any]:
    tracker = device.tracker
    config = tracker.config
    refs = {c.id for cycles in tracker.references().values() for c in cycles}
    accuracy = tracker.accuracy()
    evaluated = {e.cycle_id: e for e in device.evaluation.cycles} if device.evaluation else {}
    return {
        **live_json(device),
        "title": device.entry.title,
        "device_type": device.device_type.value,
        "currency": device.hass.config.currency,
        "price_entity": device.price_entity,
        "thresholds": {"show": config.show_confidence, "auto": config.auto_label_confidence},
        "evaluation": _evaluation_json(device.evaluation),
        "programs": list(tracker.programs),
        "accuracy": {
            "cycles": accuracy.total.cycles,
            "hits": accuracy.total.hits,
            "unsure": accuracy.total.unsure,
            "programs": {
                p: {"cycles": a.cycles, "hits": a.hits, "unsure": a.unsure}
                for p, a in accuracy.programs.items()
            },
        },
        "cycles": [
            cycle_json(c, refs, evaluation=_cycle_evaluation(evaluated.get(c.id), config.show_confidence))
            | {"split_points": [[round(a), round(b)] for a, b in quiet_stretches(c, config)[:SPLIT_POINTS]]}
            for c in reversed(tracker.history)
        ],
    }


def _devices(hass: HomeAssistant) -> list[WashRDataDevice]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _find(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> WashRDataDevice | None:
    device = next((d for d in _devices(hass) if d.entry.entry_id == msg["entry_id"]), None)
    if device is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown device")
    return device


@websocket_api.websocket_command({vol.Required("type"): "washrdata/overview"})
@callback
def ws_overview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    connection.send_result(msg["id"], {"devices": [device_json(d) for d in _devices(hass)]})


@websocket_api.websocket_command({vol.Required("type"): "washrdata/live", vol.Required("entry_id"): str})
@callback
def ws_live(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if (device := _find(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], live_json(device))


@websocket_api.websocket_command({vol.Required("type"): "washrdata/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Sends the entry id whenever a device changed. Devices loaded later are not included."""

    def forward(entry_id: str) -> Any:
        @callback
        def send() -> None:
            connection.send_message(websocket_api.event_message(msg["id"], {"entry_id": entry_id}))

        return send

    unsubs = [
        async_dispatcher_connect(hass, signal_update(d.entry.entry_id), forward(d.entry.entry_id))
        for d in _devices(hass)
    ]

    @callback
    def unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "washrdata/label",
        vol.Required("entry_id"): str,
        vol.Required("cycle_id"): str,
        vol.Required("program"): vol.Any(None, vol.All(str, vol.Strip, vol.Length(max=60))),
    }
)
@callback
def ws_label(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if (device := _find(hass, connection, msg)) is None:
        return
    try:
        device.tracker.label(msg["cycle_id"], msg["program"] or None)
    except KeyError:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown cycle")
        return
    device.changed(save_now=True)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "washrdata/split_cycle",
        vol.Required("entry_id"): str,
        vol.Required("cycle_id"): str,
        vol.Required("at_s"): vol.Coerce(float),
    }
)
@callback
def ws_split(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Splits a stored cycle in two, e.g. two washes merged by a resume."""
    if (device := _find(hass, connection, msg)) is None:
        return
    try:
        first, second = device.tracker.split(msg["cycle_id"], msg["at_s"])
    except KeyError:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown cycle")
        return
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    device.changed(save_now=True)
    connection.send_result(msg["id"], {"cycles": [first.id, second.id]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "washrdata/rename_program",
        vol.Required("entry_id"): str,
        vol.Required("program"): str,
        vol.Required("new_name"): vol.All(str, vol.Strip, vol.Length(min=1, max=60)),
    }
)
@callback
def ws_rename(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if (device := _find(hass, connection, msg)) is None:
        return
    try:
        device.tracker.rename_program(msg["program"], msg["new_name"])
    except KeyError:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown program")
        return
    except ValueError:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Program exists")
        return
    device.changed(save_now=True)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "washrdata/delete_program",
        vol.Required("entry_id"): str,
        vol.Required("program"): str,
    }
)
@callback
def ws_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if (device := _find(hass, connection, msg)) is None:
        return
    try:
        device.tracker.delete_program(msg["program"])
    except KeyError:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Unknown program")
        return
    device.changed(save_now=True)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "washrdata/set_thresholds",
        vol.Required("entry_id"): str,
        vol.Required("show"): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=0.95)),
        vol.Required("auto"): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=0.95)),
    }
)
@callback
def ws_set_thresholds(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Applies thresholds, e.g. the evaluation's recommendation; reloads the device."""
    if (device := _find(hass, connection, msg)) is None:
        return
    if msg["show"] > msg["auto"]:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "show above auto")
        return
    entry = device.entry
    options = {
        **entry.options,
        OPT_SHOW_CONFIDENCE: round(msg["show"] * 100),
        OPT_AUTO_LABEL_CONFIDENCE: round(msg["auto"] * 100),
    }
    hass.config_entries.async_update_entry(entry, options=options)
    hass.config_entries.async_schedule_reload(entry.entry_id)
    connection.send_result(msg["id"])


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    for command in (
        ws_overview,
        ws_live,
        ws_subscribe,
        ws_label,
        ws_split,
        ws_rename,
        ws_delete,
        ws_set_thresholds,
    ):
        websocket_api.async_register_command(hass, command)
