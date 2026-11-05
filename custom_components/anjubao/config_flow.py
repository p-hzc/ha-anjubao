"""配置流程：账号密码登录。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnjubaoAuthError, AnjubaoCloudApi, AnjubaoError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CREATE_COVERS,
    CONF_CREATE_SCENES,
    CONF_LAN,
    CONF_LAN_HOST,
    CONF_LAN_PORT,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    CONF_USERNAME,
    DEFAULT_CREATE_COVERS,
    DEFAULT_CREATE_SCENES,
    DEFAULT_LAN,
    DEFAULT_LAN_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENABLE_COVER_SCENE,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AnjubaoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """安居家园 Plus 账号登录。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                entry_data = await self._async_validate(user_input)
            except AnjubaoAuthError as err:
                _LOGGER.debug("登录失败: %s", err)
                errors["base"] = "invalid_auth"
            except AnjubaoError as err:
                _LOGGER.debug("连接失败: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("未知错误")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(entry_data[CONF_USER_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"安居家园 {user_input[CONF_USERNAME]}", data=entry_data
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> dict[str, Any]:
        api = AnjubaoCloudApi(
            async_get_clientsession(self.hass),
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
        )
        await api.async_login()
        if not api.user_id:
            await api.async_get_user_info()
        if not api.user_id:
            raise AnjubaoError("未能获取用户 ID")
        return {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_ACCESS_TOKEN: api.access_token,
            CONF_REFRESH_TOKEN: api.refresh_token,
            CONF_USER_ID: api.user_id,
        }

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        if user_input is not None:
            merged = {**entry.data, **user_input}
            try:
                entry_data = await self._async_validate(merged)
            except AnjubaoError:
                errors["base"] = "invalid_auth"
            else:
                self.hass.config_entries.async_update_entry(entry, data=entry_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return AnjubaoOptionsFlow(config_entry)


class AnjubaoOptionsFlow(config_entries.OptionsFlow):
    """通道与轮询设置。"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        data = {**self._entry.data, **self._entry.options}
        fields: dict[Any, Any] = {
            vol.Optional(CONF_LAN, default=data.get(CONF_LAN, DEFAULT_LAN)): cv.boolean,
            vol.Optional(CONF_LAN_HOST, default=data.get(CONF_LAN_HOST, "")): str,
            vol.Optional(
                CONF_LAN_PORT,
                default=int(data.get(CONF_LAN_PORT) or DEFAULT_LAN_PORT),
            ): vol.All(int, vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=int(data.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL),
            ): vol.All(int, vol.Range(min=15, max=3600)),
        }
        # 窗帘与场景平台暂未启用，对应的开关也不必出现（见 const.ENABLE_COVER_SCENE）
        if ENABLE_COVER_SCENE:
            fields[
                vol.Optional(
                    CONF_CREATE_COVERS,
                    default=bool(data.get(CONF_CREATE_COVERS, DEFAULT_CREATE_COVERS)),
                )
            ] = cv.boolean
            fields[
                vol.Optional(
                    CONF_CREATE_SCENES,
                    default=bool(data.get(CONF_CREATE_SCENES, DEFAULT_CREATE_SCENES)),
                )
            ] = cv.boolean
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
