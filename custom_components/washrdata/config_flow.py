"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_TYPE,
    CONF_ENERGY_SENSOR,
    CONF_POWER_SENSOR,
    DOMAIN,
    NO_PROGRAM,
    OPT_ANTI_CREASE,
    OPT_AUTO_LABEL_CONFIDENCE,
    OPT_END_DELAY_MIN,
    OPT_MAX_END_DELAY_MIN,
    OPT_PRICE_ENTITY,
    OPT_RESUME_WINDOW_MIN,
    OPT_SHOW_CONFIDENCE,
    OPT_START_POWER,
    OPT_STOP_POWER,
    build_config,
)
from .cycle_view import async_words, cycle_label, program_text, sparkline
from .engine import CycleRecord, DeviceType

CONF_CYCLE = "cycle"
CONF_PROGRAM = "program"


def _watts() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=500, step=0.5, unit_of_measurement="W", mode=selector.NumberSelectorMode.BOX
        )
    )


def _minutes(low: float, high: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low, max=high, step=0.5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
        )
    )


def _percent() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=5, max=95, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.SLIDER
        )
    )


def _sensor_fields() -> dict[Any, Any]:
    return {
        vol.Required(CONF_POWER_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER)
        ),
        vol.Optional(CONF_ENERGY_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.ENERGY)
        ),
    }


class WashRDataConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_POWER_SENSOR])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_DEVICE_TYPE, default=DeviceType.WASHER.value): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[t.value for t in DeviceType],
                        translation_key=CONF_DEVICE_TYPE,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                **_sensor_fields(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Other power sensor or energy meter; cycles, programs and learning stay."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            power = user_input[CONF_POWER_SENSOR]
            if power != entry.unique_id:
                await self.async_set_unique_id(power)
                self._abort_if_unique_id_configured()
            data = {**entry.data, CONF_POWER_SENSOR: power}
            data.pop(CONF_ENERGY_SENSOR, None)
            if energy := user_input.get(CONF_ENERGY_SENSOR):
                data[CONF_ENERGY_SENSOR] = energy
            return self.async_update_reload_and_abort(entry, unique_id=power, data=data)

        schema = self.add_suggested_values_to_schema(vol.Schema(_sensor_fields()), dict(entry.data))
        return self.async_show_form(step_id="reconfigure", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WashRDataOptionsFlow:
        return WashRDataOptionsFlow()


class WashRDataOptionsFlow(OptionsFlowWithReload):
    """Detection settings, and the cycle history to check and correct programs."""

    _cycle: CycleRecord | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "cycles"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[OPT_STOP_POWER] > user_input[OPT_START_POWER]:
                errors["base"] = "stop_above_start"
            elif user_input[OPT_SHOW_CONFIDENCE] > user_input[OPT_AUTO_LABEL_CONFIDENCE]:
                errors["base"] = "show_above_auto"
            else:
                return self.async_create_entry(data=user_input)

        config = build_config(
            DeviceType(self.config_entry.data[CONF_DEVICE_TYPE]), dict(self.config_entry.options)
        )
        schema = vol.Schema(
            {
                vol.Required(OPT_START_POWER, default=config.start_power_w): _watts(),
                vol.Required(OPT_STOP_POWER, default=config.stop_power_w): _watts(),
                vol.Required(OPT_END_DELAY_MIN, default=config.end_delay_s / 60): _minutes(1, 120),
                vol.Required(OPT_MAX_END_DELAY_MIN, default=config.max_end_delay_s / 60): _minutes(1, 180),
                vol.Required(OPT_RESUME_WINDOW_MIN, default=config.resume_window_s / 60): _minutes(0, 60),
                vol.Required(OPT_ANTI_CREASE, default=config.anti_crease): selector.BooleanSelector(),
                vol.Required(OPT_SHOW_CONFIDENCE, default=round(config.show_confidence * 100)): _percent(),
                vol.Required(
                    OPT_AUTO_LABEL_CONFIDENCE, default=round(config.auto_label_confidence * 100)
                ): _percent(),
                vol.Optional(OPT_PRICE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
                ),
            }
        )
        schema = self.add_suggested_values_to_schema(schema, user_input or dict(self.config_entry.options))
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    def _history(self) -> list[CycleRecord]:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return []
        return self.config_entry.runtime_data.tracker.history

    async def async_step_cycles(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        history = self._history()
        if user_input is not None:
            self._cycle = next((c for c in history if c.id == user_input[CONF_CYCLE]), None)
            if self._cycle is not None:
                return await self.async_step_cycle()
        if not history:
            return self.async_abort(reason="no_cycles")

        words = await async_words(self.hass)
        language = self.hass.config.language
        options = [
            selector.SelectOptionDict(value=c.id, label=cycle_label(c, words, language))
            for c in reversed(history)
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_CYCLE, default=options[0]["value"]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(
            step_id="cycles", data_schema=schema, description_placeholders={"count": str(len(history))}
        )

    async def async_step_cycle(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        cycle = self._cycle
        assert cycle is not None
        device = self.config_entry.runtime_data
        words = await async_words(self.hass)
        language = self.hass.config.language
        if user_input is not None:
            program = str(user_input[CONF_PROGRAM]).strip()
            device.tracker.label(cycle.id, None if program in ("", NO_PROGRAM) else program)
            device.changed(save_now=True)
            return await self.async_step_saved()

        options = [
            selector.SelectOptionDict(value=NO_PROGRAM, label=words[NO_PROGRAM]),
            *(selector.SelectOptionDict(value=p, label=p) for p in device.tracker.programs),
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_PROGRAM, default=cycle.program or NO_PROGRAM): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="cycle",
            data_schema=schema,
            description_placeholders={
                "cycle": cycle_label(cycle, words, language, curve=0),
                "peak": f"{round(cycle.peak_power_w)} W",
                "curve": sparkline(cycle, 40),
            },
        )

    async def async_step_saved(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._cycle is not None
        words = await async_words(self.hass)
        return self.async_show_menu(
            step_id="saved",
            menu_options=["cycles", "done"],
            description_placeholders={"program": program_text(self._cycle, words, self.hass.config.language)},
        )

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Labels are saved already; unchanged options do not reload the entry.
        return self.async_create_entry(data=dict(self.config_entry.options))
