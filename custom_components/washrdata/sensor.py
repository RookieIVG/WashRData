"""Sensors: state, current and last cycle, lifetime totals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from . import WashRDataConfigEntry
from .device import WashRDataDevice, cycle_summary
from .engine import CycleRecord, State
from .entity import WashRDataEntity


def _minutes(cycle: CycleRecord | None) -> float | None:
    if cycle is None:
        return None
    return round(cycle.duration(dt_util.utcnow().timestamp()) / 60, 1)


def _kwh(wh: float | None) -> float | None:
    return None if wh is None else round(wh / 1000, 3)


def _program(d: WashRDataDevice) -> str | None:
    """Label of the running cycle, else what recognition says."""
    cycle = d.detector.cycle
    if cycle is None:
        return None
    if cycle.program:
        return cycle.program
    return d.match.program if d.match else None


def _program_attrs(d: WashRDataDevice) -> dict[str, Any] | None:
    match = d.match
    if match is None:
        return None
    return {
        "confidence": round(match.confidence * 100),
        "manual": match.manual,
        "candidates": [{"program": p, "score": s} for p, s in match.candidates],
    }


def _recognition(d: WashRDataDevice) -> int | None:
    return round(d.match.recognition_confidence * 100) if d.match else None


def _accuracy(d: WashRDataDevice) -> int | None:
    rate = d.tracker.accuracy().total.rate
    return None if rate is None else round(rate * 100)


def _accuracy_attrs(d: WashRDataDevice) -> dict[str, Any]:
    accuracy = d.tracker.accuracy()
    return {
        "cycles": accuracy.total.cycles,
        "hits": accuracy.total.hits,
        "unsure": accuracy.total.unsure,
        "programs": {
            p: {
                "cycles": a.cycles,
                "hits": a.hits,
                "unsure": a.unsure,
                "accuracy": round(a.rate * 100) if a.rate is not None else None,
            }
            for p, a in accuracy.programs.items()
        },
    }


def _remaining_s(d: WashRDataDevice) -> float | None:
    if d.match is None or not d.match.program:
        return None
    return d.match.remaining_s


def _expected_end(d: WashRDataDevice) -> datetime | None:
    remaining = _remaining_s(d)
    if remaining is None:
        return None
    return (dt_util.utcnow() + timedelta(seconds=remaining)).replace(second=0, microsecond=0)


def _progress(d: WashRDataDevice) -> float | None:
    if _remaining_s(d) is None or d.match is None or d.match.progress is None:
        return None
    return round(d.match.progress * 100)


@dataclass(frozen=True, kw_only=True)
class WashRDataSensorDescription(SensorEntityDescription):
    value_fn: Callable[[WashRDataDevice], StateType | datetime]
    attrs_fn: Callable[[WashRDataDevice], dict[str, Any] | None] = lambda _: None


SENSORS: tuple[WashRDataSensorDescription, ...] = (
    WashRDataSensorDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=[s.value for s in State],
        value_fn=lambda d: d.detector.state.value,
    ),
    WashRDataSensorDescription(
        key="program",
        translation_key="program",
        value_fn=_program,
        attrs_fn=_program_attrs,
    ),
    WashRDataSensorDescription(
        key="recognition_confidence",
        translation_key="recognition_confidence",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_recognition,
        attrs_fn=lambda d: {"program": d.match.recognised} if d.match else None,
    ),
    WashRDataSensorDescription(
        key="recognition_accuracy",
        translation_key="recognition_accuracy",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_accuracy,
        attrs_fn=_accuracy_attrs,
    ),
    WashRDataSensorDescription(
        key="time_remaining",
        translation_key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: None if (r := _remaining_s(d)) is None else round(r / 60),
    ),
    WashRDataSensorDescription(
        key="expected_end",
        translation_key="expected_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_expected_end,
    ),
    WashRDataSensorDescription(
        key="progress",
        translation_key="progress",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_progress,
    ),
    WashRDataSensorDescription(
        key="cycle_duration",
        translation_key="cycle_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: _minutes(d.detector.cycle),
    ),
    WashRDataSensorDescription(
        key="cycle_energy",
        translation_key="cycle_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: _kwh(d.detector.cycle.energy_wh) if d.detector.cycle else None,
    ),
    WashRDataSensorDescription(
        key="last_cycle_duration",
        translation_key="last_cycle_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: _minutes(d.last_cycle),
        attrs_fn=lambda d: cycle_summary(d.last_cycle) if d.last_cycle else None,
    ),
    WashRDataSensorDescription(
        key="last_cycle_energy",
        translation_key="last_cycle_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: _kwh(d.last_cycle.energy_wh) if d.last_cycle else None,
    ),
    WashRDataSensorDescription(
        key="cycle_count",
        translation_key="cycle_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.detector.cycle_count,
    ),
    WashRDataSensorDescription(
        key="energy_total",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: _kwh(d.detector.total_energy_wh),
    ),
    WashRDataSensorDescription(
        key="end_delay",
        translation_key="end_delay",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: round(d.detector.end_delay / 60, 1),
    ),
)


def _cost(cycle: CycleRecord | None) -> float | None:
    return round(cycle.cost, 2) if cycle and cycle.cost is not None else None


# Only with a price entity configured.
COST_SENSORS: tuple[WashRDataSensorDescription, ...] = (
    WashRDataSensorDescription(
        key="cycle_cost",
        translation_key="cycle_cost",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: _cost(d.detector.cycle),
    ),
    WashRDataSensorDescription(
        key="last_cycle_cost",
        translation_key="last_cycle_cost",
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda d: _cost(d.last_cycle),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WashRDataConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    device = entry.runtime_data
    async_add_entities(WashRDataSensor(device, d) for d in SENSORS)
    if device.price_entity:
        async_add_entities(WashRDataCostSensor(device, d) for d in COST_SENSORS)


class WashRDataSensor(WashRDataEntity, SensorEntity):
    entity_description: WashRDataSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs_fn(self.device)


class WashRDataCostSensor(WashRDataSensor):
    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency
