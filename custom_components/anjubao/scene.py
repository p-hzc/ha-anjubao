"""场景平台：把网关里的场景映射成 HA 的 scene 实体。

场景来源是网关的 `scene/sync`（回复 `scene/sync_reply.scene_list`），
执行走 `scene/set`，两者都是 App 的原始做法：

    ActionUtil.normalAction(msgId, "scene/sync")   -> 拉列表
    ActionUtil.intoScene(uuid, msgId, "scene/set") -> 执行

和面板按键的关系：
    面板按键（property_type 18）执行的是同一个 scene_uuid，
    所以这里一个场景一个实体，与物理面板是「同源不同入口」，
    不会互相冲突。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AnjubaoCoordinator, channel_online

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AnjubaoCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.scenes:
        _LOGGER.debug("安居家园没有可用的场景，跳过 scene 平台")
        return
    gateway = coordinator.gateway
    async_add_entities(
        AnjubaoScene(coordinator, scene_uuid, name, gateway)
        for scene_uuid, name in coordinator.scenes.items()
    )


class AnjubaoScene(Scene):
    """网关场景。激活即下发 scene/set。

    不做 has_entity_name：场景是全局概念，实体名直接用场景名
    （scene.hui_jia / scene.li_jia），语音和自动化里都更好用。
    """

    _attr_should_poll = False
    _attr_icon = "mdi:palette"

    def __init__(
        self,
        coordinator: AnjubaoCoordinator,
        scene_uuid: str,
        name: str,
        gateway: dict[str, Any],
    ) -> None:
        self.coordinator = coordinator
        self._scene_uuid = scene_uuid
        self._attr_unique_id = f"{DOMAIN}_scene_{scene_uuid}"
        self._attr_name = name
        gwno = str(gateway.get("gwno") or "")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"gateway_{gwno or 'default'}")},
            name=f"安居家园网关 {gwno}".strip(),
            manufacturer="安居宝",
            model="智能网关",
        )

    @property
    def available(self) -> bool:
        return channel_online(self.coordinator)

    async def async_activate(self, **kwargs: Any) -> None:
        await self.coordinator.async_activate_scene(self._scene_uuid)
