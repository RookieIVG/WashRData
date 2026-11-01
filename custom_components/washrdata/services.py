"""Services to label cycles and manage programs."""

from __future__ import annotations

from collections.abc import Callable

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .device import WashRDataDevice
from .engine import CURRENT, LAST

ATTR_DEVICE_ID = "device_id"
ATTR_PROGRAM = "program"
ATTR_CYCLE = "cycle"
ATTR_NEW_NAME = "new_name"
ATTR_MERGE = "merge"

SERVICE_LABEL_CYCLE = "label_cycle"
SERVICE_CREATE_PROGRAM = "create_program"
SERVICE_RENAME_PROGRAM = "rename_program"
SERVICE_DELETE_PROGRAM = "delete_program"

_PROGRAM = vol.All(cv.string, vol.Strip, vol.Length(min=1, max=60))

SCHEMAS = {
    SERVICE_LABEL_CYCLE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_PROGRAM): vol.Any(None, vol.All(cv.string, vol.Strip)),
            vol.Optional(ATTR_CYCLE): cv.string,
        }
    ),
    SERVICE_CREATE_PROGRAM: vol.Schema(
        {vol.Required(ATTR_DEVICE_ID): cv.string, vol.Required(ATTR_PROGRAM): _PROGRAM}
    ),
    SERVICE_RENAME_PROGRAM: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required(ATTR_PROGRAM): cv.string,
            vol.Required(ATTR_NEW_NAME): _PROGRAM,
            vol.Optional(ATTR_MERGE, default=False): cv.boolean,
        }
    ),
    SERVICE_DELETE_PROGRAM: vol.Schema(
        {vol.Required(ATTR_DEVICE_ID): cv.string, vol.Required(ATTR_PROGRAM): cv.string}
    ),
}


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


def _device(hass: HomeAssistant, call: ServiceCall) -> WashRDataDevice:
    device_id = call.data[ATTR_DEVICE_ID]
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
                return entry.runtime_data
    raise _error("unknown_device", device_id=device_id)


def _label_cycle(device: WashRDataDevice, call: ServiceCall) -> None:
    tracker = device.tracker
    target = call.data.get(ATTR_CYCLE) or (CURRENT if tracker.cycle else LAST)
    program = call.data.get(ATTR_PROGRAM) or None
    try:
        tracker.label(target, program)
    except KeyError:
        raise _error("unknown_cycle", cycle=target) from None


def _create_program(device: WashRDataDevice, call: ServiceCall) -> None:
    device.tracker.create_program(call.data[ATTR_PROGRAM])


def _rename_program(device: WashRDataDevice, call: ServiceCall) -> None:
    old, new = call.data[ATTR_PROGRAM], call.data[ATTR_NEW_NAME]
    try:
        device.tracker.rename_program(old, new, merge=call.data[ATTR_MERGE])
    except KeyError:
        raise _error("unknown_program", program=old) from None
    except ValueError:
        raise _error("program_exists", program=new) from None


def _delete_program(device: WashRDataDevice, call: ServiceCall) -> None:
    try:
        device.tracker.delete_program(call.data[ATTR_PROGRAM])
    except KeyError:
        raise _error("unknown_program", program=call.data[ATTR_PROGRAM]) from None


HANDLERS: dict[str, Callable[[WashRDataDevice, ServiceCall], None]] = {
    SERVICE_LABEL_CYCLE: _label_cycle,
    SERVICE_CREATE_PROGRAM: _create_program,
    SERVICE_RENAME_PROGRAM: _rename_program,
    SERVICE_DELETE_PROGRAM: _delete_program,
}


def async_register_services(hass: HomeAssistant) -> None:
    for name, handler in HANDLERS.items():

        async def _handle(call: ServiceCall, handler=handler) -> None:
            device = _device(hass, call)
            handler(device, call)
            device.changed(save_now=True)

        hass.services.async_register(DOMAIN, name, _handle, schema=SCHEMAS[name])
