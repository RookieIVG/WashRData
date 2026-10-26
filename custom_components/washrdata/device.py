"""Connects one power sensor to a cycle detector and keeps its state."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfPower
from homeassistant.core import Event as HassEvent
from homeassistant.core import EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_TYPE,
    CONF_POWER_SENSOR,
    DOMAIN,
    EVALUATION_DELAY_S,
    OPT_PRICE_ENTITY,
    SAVE_DELAY_S,
    STORAGE_VERSION,
    TICK_INTERVAL,
    build_config,
    signal_update,
)
from .engine import CycleDetector, CycleRecord, DeviceType, Event, EventKind, MatchResult, Tracker
from .engine.evaluation import Evaluation, evaluate, fill_missing

_LOGGER = logging.getLogger(__name__)

_TO_WATTS = {UnitOfPower.WATT: 1.0, UnitOfPower.KILO_WATT: 1000.0, UnitOfPower.MILLIWATT: 0.001}


def parse_power(state: State | None) -> float | None:
    """Watts from a power sensor state, None when unavailable or invalid."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, UnitOfPower.WATT)
    return value * _TO_WATTS.get(unit, 1.0)


def parse_price(state: State | None) -> float | None:
    """Price per kWh in the main currency unit; prices in cents are converted."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = str(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "")).lower()
    return value / 100 if unit.startswith(("ct", "cent", "c/")) else value


def cycle_summary(cycle: CycleRecord) -> dict[str, Any]:
    """Event payload for a cycle, without the trace."""

    def iso(ts: float | None) -> str | None:
        return dt_util.utc_from_timestamp(ts).isoformat() if ts is not None else None

    return {
        "start": iso(cycle.start),
        "end": iso(cycle.end),
        "duration_min": round(cycle.duration(dt_util.utcnow().timestamp()) / 60, 1),
        "energy_kwh": round(cycle.energy_wh / 1000, 3),
        "peak_power_w": round(cycle.peak_power_w, 1),
        "interrupted": cycle.interrupted,
        "resumed": cycle.resumed,
        "anti_crease_bursts": cycle.anti_crease_bursts,
        "program": cycle.program,
        "program_source": cycle.program_source,
        "match_confidence": cycle.match_confidence,
        "recognised": cycle.recognised,
        "cost": round(cycle.cost, 2) if cycle.cost is not None else None,
    }


class WashRDataDevice:
    """Runtime of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.device_type = DeviceType(entry.data[CONF_DEVICE_TYPE])
        self.power_entity: str = entry.data[CONF_POWER_SENSOR]
        self.config = build_config(self.device_type, dict(entry.options))
        self.tracker = Tracker(self.config)
        self.price_entity: str | None = entry.options.get(OPT_PRICE_ENTITY)
        self.evaluation: Evaluation | None = None
        self._evaluation_unsub: Any = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[Any] = []

    @property
    def detector(self) -> CycleDetector:
        return self.tracker.detector

    @property
    def last_cycle(self) -> CycleRecord | None:
        return self.tracker.last_cycle

    @property
    def match(self) -> MatchResult | None:
        return self.tracker.match

    async def async_start(self) -> None:
        if data := await self._store.async_load():
            self.tracker = Tracker.from_dict(self.config, data)
            if self.tracker.trim_turns():
                await self._store.async_save(self._data())
        self._unsubs = [
            async_track_state_change_event(self.hass, self.power_entity, self._on_state),
            async_track_state_report_event(self.hass, self.power_entity, self._on_state),
            async_track_time_interval(self.hass, self._on_tick, TICK_INTERVAL),
        ]
        if self.price_entity:
            self._unsubs.append(async_track_state_change_event(self.hass, self.price_entity, self._on_price))
            self.detector.price = parse_price(self.hass.states.get(self.price_entity))
        self.schedule_evaluation()
        state = self.hass.states.get(self.power_entity)
        self._handle(self.tracker.update(dt_util.utcnow().timestamp(), parse_power(state)))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._evaluation_unsub:
            self._evaluation_unsub()
            self._evaluation_unsub = None
        await self._store.async_save(self._data())

    @callback
    def _on_state(self, event: HassEvent[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if state is None:
            return
        # A tick may have run between the state write and this callback; the
        # sample is still the newest reading, so it must not be dropped as stale.
        ts = max(state.last_reported_timestamp, self.detector.last_ts or 0.0)
        self._handle(self.tracker.update(ts, parse_power(state)))

    @callback
    def _on_price(self, event: HassEvent[EventStateChangedData]) -> None:
        # Energy up to now is charged at the old price before the new one applies.
        self._handle(self.tracker.tick(dt_util.utcnow().timestamp()))
        self.detector.price = parse_price(event.data["new_state"])

    @callback
    def _on_tick(self, now: datetime) -> None:
        self._handle(self.tracker.tick(now.timestamp()))

    @callback
    def changed(self, save_now: bool = False) -> None:
        """Persist and refresh entities after a change made outside a sample."""
        if save_now:
            self.hass.async_create_task(self._store.async_save(self._data()))
            self.schedule_evaluation()
        else:
            self._store.async_delay_save(self._data, SAVE_DELAY_S)
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    @callback
    def _handle(self, events: list[Event]) -> None:
        for event in events:
            self._fire(event)
        self.changed(save_now=any(e.kind == EventKind.FINISHED for e in events))

    @callback
    def _fire(self, event: Event) -> None:
        cycle = event.cycle
        _LOGGER.debug("%s: %s", self.entry.title, event.kind)
        data: dict[str, Any] = {
            "entry_id": self.entry.entry_id,
            "name": self.entry.title,
            "device_type": self.device_type.value,
            "cycle": cycle_summary(cycle) if cycle else None,
        }
        if event.kind == EventKind.PROGRAM_DETECTED and (match := self.tracker.match):
            data["program"] = match.program
            data["confidence"] = round(match.confidence, 2)
            data["remaining_min"] = round(match.remaining_s / 60) if match.remaining_s is not None else None
        self.hass.bus.async_fire(f"{DOMAIN}_{event.kind}", data)

    @callback
    def schedule_evaluation(self) -> None:
        """Evaluate again once changes (finished cycles, labels) have settled."""
        if self._evaluation_unsub:
            self._evaluation_unsub()
        self._evaluation_unsub = async_call_later(self.hass, EVALUATION_DELAY_S, self._async_evaluate)

    async def _async_evaluate(self, _now: datetime | None = None) -> None:
        self._evaluation_unsub = None
        tracker = self.tracker
        # The replay takes seconds: run it in a thread, on a copy.
        cycles = [CycleRecord.from_dict(c.to_dict()) for c in tracker.history]
        self.evaluation = await self.hass.async_add_executor_job(evaluate, cycles, list(tracker.programs))
        if fill_missing(tracker.history, self.evaluation):
            self._store.async_delay_save(self._data, SAVE_DELAY_S)
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    def _data(self) -> dict[str, Any]:
        return self.tracker.to_dict()
