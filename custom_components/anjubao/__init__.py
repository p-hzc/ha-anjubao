"""安居家园 Plus HA 集成。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnjubaoCloudApi, AnjubaoError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import AnjubaoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.COVER,
    Platform.SCENE,
    Platform.BUTTON,
]

SERVICE_SEND_COMMAND = "send_command"
ATTR_SUBJECT = "subject"
ATTR_PAYLOAD = "payload"
ATTR_WAIT = "wait"
ATTR_ENTRY_ID = "entry_id"

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SUBJECT): cv.string,
        vol.Optional(ATTR_PAYLOAD, default={}): dict,
        vol.Optional(ATTR_WAIT, default=True): cv.boolean,
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = {**entry.data, **entry.options}
    api = AnjubaoCloudApi(
        async_get_clientsession(hass),
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
        access_token=data.get(CONF_ACCESS_TOKEN),
        refresh_token=data.get(CONF_REFRESH_TOKEN),
        user_id=data.get(CONF_USER_ID),
    )
    coordinator = AnjubaoCoordinator(hass, api, data)

    try:
        await coordinator.async_setup()
    except AnjubaoError as err:
        raise ConfigEntryNotReady(str(err)) from err

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # MQTT/LAN 线程在 async_setup 里已经起起来了，首次刷新失败必须收回，
        # 否则 HA 会不断重试并堆积连接。
        await hass.async_add_executor_job(coordinator.stop)
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinators: dict[str, AnjubaoCoordinator] = hass.data.get(DOMAIN, {})
        coordinator = coordinators.pop(entry.entry_id, None)
        if coordinator is not None:
            await hass.async_add_executor_job(coordinator.stop)
        if not coordinators:
            hass.services.async_remove(DOMAIN, SERVICE_SEND_COMMAND)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _pick_coordinator(hass: HomeAssistant, entry_id: str | None) -> AnjubaoCoordinator:
    coordinators: dict[str, AnjubaoCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("安居家园集成尚未加载")
    if entry_id:
        if entry_id not in coordinators:
            raise HomeAssistantError(f"找不到配置项 {entry_id}")
        return coordinators[entry_id]
    return next(iter(coordinators.values()))


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        return

    async def _async_send_command(call: ServiceCall) -> None:
        coordinator = _pick_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
        subject = call.data[ATTR_SUBJECT]
        try:
            await coordinator.async_send_raw(
                subject,
                call.data.get(ATTR_PAYLOAD) or {},
                wait=call.data.get(ATTR_WAIT, True),
            )
        except (AnjubaoError, HomeAssistantError) as err:
            # 转成 HomeAssistantError，前端才会把原因显示出来，而不是笼统的 Unknown error
            raise HomeAssistantError(f"{subject} 发送失败：{err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, _async_send_command, schema=SEND_COMMAND_SCHEMA
    )
