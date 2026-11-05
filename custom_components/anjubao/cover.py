"""窗帘实体：每个窗帘电机回路（dev_type 31）一个。

App 的窗帘卡片只有「开 / 关 / 停」三个按钮，没有开度滑块，
所以这里只实现 OPEN / CLOSE / STOP 三个特性。
"""
from __future__ import annotations

import logging

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CREATE_COVERS,
    CURTAIN_ACTION_CLOSE,
    CURTAIN_ACTION_OPEN,
    CURTAIN_ACTION_STOP,
    DEFAULT_CREATE_COVERS,
    DOMAIN,
)
from .coordinator import AnjubaoCoordinator, Endpoint
from .entity import AnjubaoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AnjubaoCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = {**entry.data, **entry.options}
    if not data.get(CONF_CREATE_COVERS, DEFAULT_CREATE_COVERS):
        _LOGGER.debug("窗帘实体在选项中被关闭，跳过创建")
        return
    async_add_entities(
        AnjubaoCurtain(coordinator, endpoint) for endpoint in coordinator.covers
    )


class AnjubaoCurtain(AnjubaoEntity, CoverEntity):
    """安居宝窗帘电机回路。"""

    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    # 网关不上报位置/开合度，界面按"假定状态"处理
    _attr_assumed_state = True

    @property
    def is_closed(self) -> bool | None:
        """property_type 0 在窗帘回路里表示开合：1=开，0=关，未知返回 None。"""
        state = self.coordinator.data.onoff(self.endpoint.mac, self.endpoint.index)
        return None if state is None else not state

    async def async_open_cover(self, **kwargs) -> None:
        await self.coordinator.async_send_curtain(
            self.endpoint.mac, self.endpoint.index, CURTAIN_ACTION_OPEN
        )

    async def async_close_cover(self, **kwargs) -> None:
        await self.coordinator.async_send_curtain(
            self.endpoint.mac, self.endpoint.index, CURTAIN_ACTION_CLOSE
        )

    async def async_stop_cover(self, **kwargs) -> None:
        await self.coordinator.async_send_curtain(
            self.endpoint.mac, self.endpoint.index, CURTAIN_ACTION_STOP
        )
