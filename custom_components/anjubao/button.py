"""按键平台：把场景面板的每个按键做成 button 实体。

为什么不是 switch/light：
    这类按键在网关里绑定了场景（endpoint 的 properties[0] 是
    {"property_type": 18, "value": "<scene_uuid>"}），按下去执行的是
    「一个场景」，没有开/关状态。做成有状态的实体只会得到一堆假开关，
    所以按主流做法给 button —— 按一下触发，无状态。

实体命名：
    面板设备的 devname 往往都叫「情景面板」，四个键全同名不可用，
    所以用绑定场景的名字（回家/离家/睡觉/用餐），设备名仍是面板名，
    最终显示为「门口4控 回家」。
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AnjubaoCoordinator, Endpoint
from .entity import AnjubaoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AnjubaoCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.scene_keys:
        return
    async_add_entities(
        AnjubaoSceneKey(coordinator, endpoint) for endpoint in coordinator.scene_keys
    )


class AnjubaoSceneKey(AnjubaoEntity, ButtonEntity):
    """面板上的一个场景按键。"""

    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, coordinator: AnjubaoCoordinator, endpoint: Endpoint) -> None:
        super().__init__(coordinator, endpoint)
        self._attr_unique_id = f"{endpoint.mac}_{endpoint.index}_scene"
        # 优先用场景名，退化时才用 devname
        self._attr_name = coordinator.scene_name(endpoint.scene_uuid) or endpoint.name

    async def async_press(self) -> None:
        await self.coordinator.async_activate_scene(self.endpoint.scene_uuid)
