"""灯实体：每个可控回路一个。

开关类（switch / socket / 未知类型）只有开/关；
调光类（dev_type 34，App 的 DimmerHolder 会同时发 property 0 和 property 2）额外带亮度。
"""
from __future__ import annotations

import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AnjubaoCoordinator, Endpoint
from .devtypes import is_dimmer
from .entity import AnjubaoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AnjubaoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AnjubaoLight(coordinator, endpoint) for endpoint in coordinator.endpoints
    )


class AnjubaoLight(AnjubaoEntity, LightEntity):
    """安居宝开关/灯控回路。

    灯实体直接用「灯自己的名字」，不拼面板名（`has_entity_name = False`）：
    设备叫「餐厅三控」是电工面板的编号，用户和小爱要的是「客厅顶灯」。
    窗帘 / 面板按键仍保留设备名前缀，因为它们的回路名只是「1 / 2 / 回家」，
    单独拿出来没有意义。
    """

    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF
    _attr_has_entity_name = False

    def __init__(self, coordinator: AnjubaoCoordinator, endpoint: Endpoint) -> None:
        super().__init__(coordinator, endpoint)
        # 单回路设备网关不给回路名，退回设备名；
        # 多回路设备若网关也没给名字（只会生成「回路N」），补上设备名避免重名。
        self._attr_name = endpoint.name or endpoint.device_name
        if self._attr_name.startswith("回路"):
            self._attr_name = f"{endpoint.device_name} {self._attr_name}"
        self._dimmer = is_dimmer(endpoint.dev_type)
        if self._dimmer:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.onoff(self.endpoint.mac, self.endpoint.index)

    @property
    def brightness(self) -> int | None:
        """网关亮度是 0–100，HA 要 0–255。"""
        if not self._dimmer:
            return None
        level = self.coordinator.data.level(self.endpoint.mac, self.endpoint.index)
        return None if level is None else round(level * 255 / 100)

    async def async_turn_on(self, **kwargs) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if self._dimmer and brightness is not None:
            await self.coordinator.async_send_level(
                self.endpoint.mac,
                self.endpoint.index,
                round(brightness * 100 / 255),
            )
            return
        await self.coordinator.async_send_onoff(
            self.endpoint.mac, self.endpoint.index, True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_onoff(
            self.endpoint.mac, self.endpoint.index, False
        )
