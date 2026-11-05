"""实体基类。"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AnjubaoCoordinator, Endpoint, channel_online


class AnjubaoEntity(CoordinatorEntity[AnjubaoCoordinator]):
    """所有实体共用的设备信息与唯一 ID。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AnjubaoCoordinator, endpoint: Endpoint) -> None:
        super().__init__(coordinator)
        self.endpoint = endpoint
        self._attr_unique_id = f"{endpoint.mac}_{endpoint.index}"
        # 单回路设备不设实体名，直接沿用设备名；多回路才用「灯1 / 灯2」。
        self._attr_name = endpoint.name or None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, endpoint.mac)},
            name=endpoint.device_name,
            manufacturer="安居宝",
            model=endpoint.model or None,
            suggested_area=endpoint.room or None,
        )

    @property
    def available(self) -> bool:
        """任一条通道在线就算可用。"""
        return super().available and channel_online(self.coordinator)
