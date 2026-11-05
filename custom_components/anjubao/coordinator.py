"""数据协调器：设备清单 + 通道选择（局域网优先，云端兜底）+ 状态聚合。"""
from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AnjubaoCloudApi, AnjubaoError
from .const import (
    CONF_LAN,
    CONF_LAN_HOST,
    CONF_LAN_PORT,
    CONF_SCAN_INTERVAL,
    CONF_CREATE_SCENES,
    CURTAIN_ACTION_STOP,
    CURTAIN_STOP_SERVICE_TYPE,
    DEFAULT_CREATE_SCENES,
    DEFAULT_LAN_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEV_TYPE_CURTAIN,
    DOMAIN,
    PLACEHOLDER_ROOMS,
    PROP_LEVEL,
    PROP_ONOFF,
    PROP_SCENE_UUID,
    SUBJECT_PROPERTY_GET,
    SUBJECT_PROPERTY_SET,
    SUBJECT_SCENE_SET,
    SUBJECT_SCENE_SYNC,
    SUBJECT_SERVICE_CALL,
)
from .devtypes import CAT_CURTAIN, DEVICE_TYPES, is_light, kind_of
from .lan import AnjubaoLan, LanError
from .mqtt import AnjubaoMqtt
from .protocol import build_property_set, build_scene_set, build_service_call

_LOGGER = logging.getLogger(__name__)

TRANSPORT_LAN = "lan"
TRANSPORT_CLOUD = "cloud"
TRANSPORT_NONE = "none"


def _scene_uuid_of(endpoint: dict[str, Any]) -> str:
    """读面板按键绑定的场景 uuid。

    App 的 Panel33Holder.isBinding() 判定：取 endpoints[i].properties[0]，
    若 property_type == 18 且 value 非空，则该按键绑定了一个场景。
    """
    for prop in endpoint.get("properties") or []:
        try:
            prop_type = int(prop.get("property_type", -1))
        except (TypeError, ValueError):
            continue
        if prop_type == PROP_SCENE_UUID:
            value = prop.get("value")
            return str(value).strip() if value else ""
    return ""


def channel_online(coordinator: AnjubaoCoordinator) -> bool:
    """任一条通道在线即视为可用。"""
    lan_ok = coordinator.lan is not None and coordinator.lan.connected
    cloud_ok = coordinator.mqtt is not None and coordinator.mqtt.connected
    return lan_ok or cloud_ok


@dataclass
class Endpoint:
    """设备的一个回路（或场景面板的一个按键）。"""

    mac: str
    index: int
    name: str
    room: str
    dev_type: int
    device_name: str
    model: str
    # 非空表示这个按键绑定了一个场景（properties[0].property_type == 18）
    scene_uuid: str = ""

    @property
    def is_scene_key(self) -> bool:
        return bool(self.scene_uuid)


@dataclass
class AnjubaoSnapshot:
    """一次状态快照。"""

    states: dict[str, dict[int, dict[int, Any]]] = field(default_factory=dict)

    def onoff(self, mac: str, index: int) -> bool | None:
        value = self.states.get(mac, {}).get(index, {}).get(PROP_ONOFF)
        if value is None:
            return None
        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return None

    def level(self, mac: str, index: int) -> int | None:
        value = self.states.get(mac, {}).get(index, {}).get(PROP_LEVEL)
        return None if value is None else int(value)


class AnjubaoCoordinator(DataUpdateCoordinator[AnjubaoSnapshot]):
    """同时管理局域网 / 云端两条通道与状态聚合。"""

    def __init__(
        self,
        hass: HomeAssistant,
        api: AnjubaoCloudApi,
        entry_data: dict[str, Any],
    ) -> None:
        interval = int(entry_data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.devices: list[dict[str, Any]] = []
        self.endpoints: list[Endpoint] = []
        self.covers: list[Endpoint] = []
        # 场景面板按键（绑定场景的回路），单独一桶
        self.scene_keys: list[Endpoint] = []
        # scene_uuid -> 场景名（来自 scene/sync）
        self.scenes: dict[str, str] = {}
        self.home_id: str = ""
        self.gateway: dict[str, Any] = {}
        self._snapshot = AnjubaoSnapshot()
        self.mqtt: AnjubaoMqtt | None = None
        self.lan: AnjubaoLan | None = None
        self._lock = asyncio.Lock()
        self._entry_data = entry_data
        self._transport = TRANSPORT_NONE
        self._cloud_ready = False
        self._ever_ok = False

    # ------------------------------------------------------------------ 初始化
    async def async_setup(self) -> None:
        """拉取设备清单并建立通道。"""
        if not self.api.access_token:
            await self.api.async_login()
        # getUserInfo 的 datas.id 才是 MQTT / appauth 用的 userId
        await self.api.async_get_user_info()

        homes = await self.api.async_get_homes()
        if not homes:
            raise AnjubaoError("账号下没有家庭")
        self.home_id = homes[0]["id"]

        gateways = await self.api.async_get_gateways(self.home_id)
        if not gateways:
            raise AnjubaoError("家庭下没有网关")
        self.gateway = gateways[0]
        # 云端会记录网关最近一次上报的局域网地址，供局域通道兜底使用
        setattr(self.api, "gateway", self.gateway)

        # HTTP 设备接口不返回面板按键的 property 18（场景绑定），
        # 先拿一份基础清单，通道建好后再用通道查询补全属性。
        self.devices = await self.api.async_get_devices(self.gateway["id"])

        await self._async_open_transport()
        await self._async_refresh_topology()
        self._build_endpoints()

    async def _async_refresh_topology(self) -> None:
        """用已建立的通道补全设备属性，并拉取场景列表。

        这一步是场景面板能不能用的关键：HTTP 的 device/pageList 对
        type 28 面板返回 `properties: []`，而 devicelistproperty/get
        会带上 `{"property_type":18,"value":"gohome_xxx"}` 这样的场景绑定。
        """
        transport = await self._ensure_transport()
        if transport == TRANSPORT_NONE:
            _LOGGER.warning("安居家园没有可用通道，跳过属性补全")
            return

        try:
            reply = await self.hass.async_add_executor_job(self._query_states)
        except (AnjubaoError, LanError) as err:
            _LOGGER.warning("安居家园读取设备属性失败: %s", err)
        else:
            devicelist = reply.get("devicelist") or []
            if devicelist:
                self.devices = devicelist
            self._merge_devicelist(reply)

        if not bool(self._entry_data.get(CONF_CREATE_SCENES, DEFAULT_CREATE_SCENES)):
            return

        try:
            reply = await self.hass.async_add_executor_job(self._query_scenes)
        except (AnjubaoError, LanError) as err:
            _LOGGER.warning("安居家园读取场景列表失败: %s", err)
            return
        scenes: dict[str, str] = {}
        for item in reply.get("scene_list") or []:
            uuid = str(item.get("scene_uuid") or "").strip()
            if not uuid:
                continue
            scenes[uuid] = str(item.get("scene_name") or "").strip() or uuid[:12]
        self.scenes = scenes
        _LOGGER.debug("安居家园读取到 %s 个场景: %s", len(scenes), list(scenes.values()))

    async def _async_open_transport(self) -> None:
        """优先局域网，连不上再走云端 MQTT。"""
        lan_enabled = bool(self._entry_data.get(CONF_LAN, True))
        if lan_enabled and self.lan is None:
            self.lan = AnjubaoLan(
                self.api,
                asyncio.get_running_loop(),
                on_report=self._handle_report,
                on_connection=self._handle_connection,
                host=self._entry_data.get(CONF_LAN_HOST) or None,
                port=int(self._entry_data.get(CONF_LAN_PORT) or DEFAULT_LAN_PORT),
            )
            self.lan.start(
                self.api.user_id,
                self.home_id,
                (int(self.gateway.get("gwtype", 35)), str(self.gateway.get("gwno", ""))),
            )
            if await self._wait(lambda: self.lan is not None and self.lan.connected, 12):
                self._transport = TRANSPORT_LAN
                _LOGGER.info("安居家园使用局域网直连（%s）", self.gateway.get("gwno"))
                return
            _LOGGER.info("安居家园局域网未发现网关，改用厂商云")

        if not self._cloud_ready:
            await self._async_open_cloud()
        if self.mqtt is not None and await self._wait(lambda: self.mqtt is not None and self.mqtt.connected, 20):
            self._transport = TRANSPORT_CLOUD
            self._cloud_ready = True
            return

        if self._transport == TRANSPORT_NONE:
            raise AnjubaoError("局域网与厂商云均无法连接")

    async def _async_open_cloud(self) -> None:
        if self.mqtt is not None:
            return
        self.mqtt = AnjubaoMqtt(
            self.api,
            asyncio.get_running_loop(),
            on_report=self._handle_report,
            on_connection=self._handle_connection,
            # 用户自带证书放在 <config>/anjubao_certs/ 下即可覆盖内置的厂商证书
            cert_dir=self.hass.config.path("anjubao_certs"),
        )
        self.mqtt.start(
            self.api.user_id,
            self.home_id,
            (int(self.gateway.get("gwtype", 35)), str(self.gateway.get("gwno", ""))),
        )

    @staticmethod
    async def _wait(cond, seconds: float) -> bool:
        for _ in range(int(seconds * 2)):
            if cond():
                return True
            await asyncio.sleep(0.5)
        return cond()

    def _build_endpoints(self) -> None:
        eps: list[Endpoint] = []
        covers: list[Endpoint] = []
        keys: list[Endpoint] = []
        skipped: dict[int, str] = {}
        for device in self.devices:
            mac = device.get("mac")
            if not mac:
                continue
            dev_type = int(device.get("type") or device.get("dev_type") or 0)
            kind = kind_of(dev_type)
            device_name = device.get("name") or mac[-6:]
            for endpoint in device.get("endpoints") or []:
                room = (endpoint.get("roomname") or "").strip()
                item = Endpoint(
                    mac=mac,
                    index=int(endpoint.get("index", 1)),
                    name=endpoint.get("devname") or f"回路{endpoint.get('index')}",
                    room="" if room in PLACEHOLDER_ROOMS else room,
                    dev_type=dev_type,
                    device_name=device_name,
                    model=str(device.get("module") or device.get("model") or ""),
                    scene_uuid=_scene_uuid_of(endpoint),
                )
                # 判定顺序很重要：一个面板可能是「开关键 + 场景键」混排
                # （如 Panel33Holder 的 1/2/3 是开关，4/5/6 绑场景），
                # 所以先看这个按键有没有绑场景，再看设备类型。
                if item.is_scene_key:
                    keys.append(item)
                elif kind.category == CAT_CURTAIN:
                    covers.append(item)
                elif is_light(dev_type):
                    eps.append(item)
                else:
                    skipped.setdefault(dev_type, kind.label)
        self.endpoints = eps
        self.covers = covers
        self.scene_keys = keys
        _LOGGER.debug(
            "安居家园发现 %s 个设备 / %s 个灯回路 / %s 个窗帘 / %s 个场景按键",
            len(self.devices),
            len(eps),
            len(covers),
            len(keys),
        )
        if skipped:
            _LOGGER.debug(
                "安居家园跳过无控制能力的设备类型: %s",
                ", ".join(f"{t}({label})" for t, label in sorted(skipped.items())),
            )
        unknown = sorted({int(d.get("type") or d.get("dev_type") or 0) for d in self.devices} - set(DEVICE_TYPES))
        unknown = [t for t in unknown if t]
        if unknown:
            _LOGGER.info("安居家园遇到未收录的设备类型 %s，已按开关处理", unknown)

    # ------------------------------------------------------------------ 通道
    @property
    def transport(self) -> str:
        return self._transport

    def _lan_ready(self) -> bool:
        return self.lan is not None and self.lan.connected

    def _cloud_connected(self) -> bool:
        return self.mqtt is not None and self.mqtt.connected

    async def _ensure_transport(self) -> str:
        """返回当前可用通道；局域通道掉线时自动回落云端。"""
        if self._lan_ready():
            if self._transport != TRANSPORT_LAN:
                _LOGGER.info("安居家园局域网恢复，切回直连")
            self._transport = TRANSPORT_LAN
            return TRANSPORT_LAN
        if self._cloud_connected():
            if self._transport == TRANSPORT_LAN:
                _LOGGER.warning("安居家园局域网断开，回落到厂商云")
            self._transport = TRANSPORT_CLOUD
            self._cloud_ready = True
            return TRANSPORT_CLOUD
        # 两条都不通：局域网线程自己会重连；云端没起过就起一次
        if not self._cloud_ready:
            try:
                await self._async_open_cloud()
            except AnjubaoError as err:
                _LOGGER.debug("启动云端通道失败: %s", err)
        self._transport = TRANSPORT_NONE
        return TRANSPORT_NONE

    def _query_states(self) -> dict[str, Any]:
        if self._transport == TRANSPORT_LAN and self.lan is not None:
            return self.lan.query_states(timeout=12)
        if self.mqtt is not None:
            return self.mqtt.request(SUBJECT_PROPERTY_GET, None, timeout=12)
        raise AnjubaoError("没有可用通道")

    def _query_scenes(self) -> dict[str, Any]:
        """拉取场景列表（scene/sync -> scene/sync_reply）。"""
        if self._transport == TRANSPORT_LAN and self.lan is not None:
            return self.lan.request(SUBJECT_SCENE_SYNC, None, timeout=12)
        if self.mqtt is not None:
            return self.mqtt.request(SUBJECT_SCENE_SYNC, None, timeout=12)
        raise AnjubaoError("没有可用通道")

    # ------------------------------------------------------------------ 状态
    async def _async_update_data(self) -> AnjubaoSnapshot:
        transport = await self._ensure_transport()
        if transport == TRANSPORT_NONE:
            if not self._ever_ok:
                raise UpdateFailed("局域网与厂商云均未就绪")
            # 通道恢复中，保留上一份快照，等下次轮询
            return self._snapshot
        try:
            reply = await self.hass.async_add_executor_job(self._query_states)
        except (AnjubaoError, LanError) as err:
            _LOGGER.debug("安居家园查询状态失败: %s", err)
            if not self._ever_ok:
                raise UpdateFailed(str(err)) from err
            return self._snapshot
        self._merge_devicelist(reply)
        self._ever_ok = True
        return self._snapshot

    def _merge_devicelist(self, reply: dict[str, Any]) -> None:
        states = self._snapshot.states
        for device in reply.get("devicelist") or []:
            mac = device.get("mac")
            if not mac:
                continue
            mac_states = states.setdefault(mac, {})
            for endpoint in device.get("endpoints") or []:
                index = endpoint.get("index")
                if not isinstance(index, int):
                    continue
                props = mac_states.setdefault(index, {})
                for prop in endpoint.get("properties") or []:
                    props[int(prop.get("property_type", 0))] = prop.get("value")

    def _handle_report(self, mac: str, index: int, prop_type: int, value: Any) -> None:
        """通道线程回调：网关主动上报。"""
        props = self._snapshot.states.setdefault(mac, {}).setdefault(index, {})
        props[prop_type] = value
        self._ever_ok = True
        self.async_set_updated_data(self._snapshot)

    def _handle_connection(self, connected: bool) -> None:
        _LOGGER.debug("安居家园通道状态 -> %s", connected)

    # ------------------------------------------------------------------ 控制
    async def async_send_onoff(self, mac: str, index: int, on: bool) -> None:
        await self._async_send_properties(mac, index, {PROP_ONOFF: 1 if on else 0})

    async def async_send_level(self, mac: str, index: int, level: int) -> None:
        await self._async_send_properties(
            mac, index, {PROP_ONOFF: 1, PROP_LEVEL: max(0, min(100, level))}
        )

    async def async_send_curtain(self, mac: str, index: int, action: int) -> None:
        """窗帘：开/关 = device/property_set，停 = service/call。

        与 App 的 CurtainHolder.controlDevice(value, index) 完全一致：
          value 1 -> endpoints[{index, properties[{property_type:0, value:1}]}]
          value 0 -> 同上，value 0
          value 3 -> subject service/call，endpoints[{index, service_type:1, value:1}]
        """
        if action == CURTAIN_ACTION_STOP:
            await self.async_send_raw(
                SUBJECT_SERVICE_CALL,
                build_service_call(
                    mac,
                    DEV_TYPE_CURTAIN,
                    index,
                    CURTAIN_STOP_SERVICE_TYPE,
                    1,
                ),
            )
            # 停在半途，真实位置未知，清掉开关状态让实体回到 unknown
            states = self._snapshot.states.get(mac, {}).get(index)
            if states is not None:
                states.pop(PROP_ONOFF, None)
            self.async_set_updated_data(self._snapshot)
            return
        await self._async_send_properties(mac, index, {PROP_ONOFF: int(action)})

    def _dev_type(self, mac: str) -> int:
        for device in self.devices:
            if device.get("mac") == mac:
                return int(device.get("type") or device.get("dev_type") or 0)
        return 0

    # ------------------------------------------------------------------ 场景
    def scene_name(self, scene_uuid: str) -> str:
        return self.scenes.get(scene_uuid) or ""

    async def async_activate_scene(self, scene_uuid: str) -> None:
        """执行场景。

        与 App 的 ActionUtil.intoScene(uuid, msgId, "scene/set") 完全一致：
          {"subject":"scene/set","msg_id":N,"scene_uuid":"gohome_xxx"}
        局域网路由：/lan/{gwtype}/{gwno}/scene/set
        """
        if not scene_uuid:
            raise AnjubaoError("场景 uuid 为空")
        reply = await self.async_send_raw(SUBJECT_SCENE_SET, build_scene_set(scene_uuid))
        code = (reply or {}).get("code")
        if code not in (None, 0, 200):
            raise AnjubaoError(
                f"执行场景失败: {(reply or {}).get('message') or reply}"
            )

    async def _async_send_properties(
        self, mac: str, index: int, properties: dict[int, Any]
    ) -> None:
        dev_type = self._dev_type(mac)
        async with self._lock:
            await self._ensure_transport()
            if self._transport == TRANSPORT_LAN and self.lan is not None:
                try:
                    await self.hass.async_add_executor_job(
                        functools.partial(
                            self.lan.set_properties, mac, dev_type, index, properties, 10
                        )
                    )
                except LanError as err:
                    _LOGGER.warning("安居家园局域网控制失败（%s），改走厂商云", err)
                    if not self._cloud_ready:
                        await self._async_open_cloud()
                    if not await self._wait(self._cloud_connected, 15):
                        raise AnjubaoError("局域网控制失败且云端未连接") from err
                    self._transport = TRANSPORT_CLOUD
                    await self.hass.async_add_executor_job(
                        functools.partial(
                            self._cloud_set, mac, dev_type, index, properties
                        )
                    )
            elif self._transport == TRANSPORT_CLOUD:
                await self.hass.async_add_executor_job(
                    functools.partial(self._cloud_set, mac, dev_type, index, properties)
                )
            else:
                raise AnjubaoError("没有可用通道，无法控制设备")

        # 乐观更新，等 property_report 覆盖
        props = self._snapshot.states.setdefault(mac, {}).setdefault(index, {})
        props.update(properties)
        self.async_set_updated_data(self._snapshot)

    def _cloud_set(
        self, mac: str, dev_type: int, index: int, properties: dict[int, Any]
    ) -> dict[str, Any]:
        if self.mqtt is None:
            raise AnjubaoError("MQTT 未初始化")
        body = build_property_set(mac, dev_type, index, properties)
        return self.mqtt.request(SUBJECT_PROPERTY_SET, body, timeout=12)

    async def async_send_raw(
        self, subject: str, body: dict[str, Any], wait: bool = True
    ) -> dict[str, Any] | None:
        """通用指令：局域网优先，失败回落云端。返回网关回包（不等待时返回 None）。"""
        await self._ensure_transport()
        if self._transport == TRANSPORT_LAN and self.lan is not None:
            func = self.lan.request if wait else self.lan.fire
            try:
                reply = await self.hass.async_add_executor_job(
                    functools.partial(func, subject, body)
                )
                return reply if wait else None
            except LanError as err:
                _LOGGER.warning("安居家园局域网指令失败（%s），改走厂商云", err)
        if self.mqtt is None:
            if not self._cloud_ready:
                await self._async_open_cloud()
            if not await self._wait(self._cloud_connected, 15):
                raise AnjubaoError("没有可用通道")
            self._transport = TRANSPORT_CLOUD
        if wait:
            return await self.hass.async_add_executor_job(
                functools.partial(self.mqtt.request, subject, body, timeout=12)
            )
        await self.hass.async_add_executor_job(
            functools.partial(self.mqtt.fire, subject, body)
        )
        return None

    def stop(self) -> None:
        if self.lan is not None:
            self.lan.stop()
        if self.mqtt is not None:
            self.mqtt.stop()
