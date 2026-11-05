"""诊断信息：给用户报 bug 时一键下载。

「设置 → 设备与服务 → 安居家园 → 下载诊断信息」导出的 JSON 会包含通道状态、
设备/回路统计和类型分布，但**不含密码、令牌、完整手机号与完整 MAC**。
排查「为什么灯没出现」「为什么走云端」这类问题，这份文件就够了。
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_LAN,
    CONF_LAN_HOST,
    CONF_LAN_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
    ENABLE_COVER_SCENE,
)
from .coordinator import AnjubaoCoordinator
from .devtypes import DEVICE_TYPES, kind_of


def _mask_phone(value: Any) -> str:
    """13812345678 -> 138****5678。"""
    text = str(value or "")
    if len(text) < 7:
        return "***"
    return f"{text[:3]}****{text[-4:]}"


def _mask_mac(value: Any) -> str:
    """00158d000657e99f -> ******0657e99f。"""
    text = str(value or "")
    if len(text) <= 8:
        return text
    return f"{'*' * (len(text) - 8)}{text[-8:]}"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: AnjubaoCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return {"error": "集成未加载"}

    options = {**entry.data, **entry.options}
    gateway = coordinator.gateway or {}

    type_counts: dict[str, int] = {}
    for device in coordinator.devices:
        dev_type = int(device.get("dev_type") or device.get("type") or 0)
        key = f"{dev_type}:{kind_of(dev_type).label}"
        type_counts[key] = type_counts.get(key, 0) + 1

    # 注意：HTTP 接口用 type，MQTT 的 devicelist 用 dev_type，两个都要看
    unknown_types = sorted(
        {
            int(d.get("dev_type") or d.get("type") or 0)
            for d in coordinator.devices
        }
        - set(DEVICE_TYPES)
    )
    unknown_types = [t for t in unknown_types if t]

    return {
        "version": entry.version,
        "account": _mask_phone(options.get(CONF_USERNAME)),
        "entry": {
            "unique_id_tail": str(entry.unique_id or "")[-4:],
            "has_access_token": bool(coordinator.api.access_token),
            "has_refresh_token": bool(coordinator.api.refresh_token),
        },
        "options": {
            CONF_LAN: options.get(CONF_LAN),
            CONF_LAN_HOST: options.get(CONF_LAN_HOST) or "(自动发现)",
            CONF_LAN_PORT: options.get(CONF_LAN_PORT),
            CONF_SCAN_INTERVAL: options.get(CONF_SCAN_INTERVAL),
        },
        "transport": {
            "active": coordinator.transport,
            "lan_connected": bool(coordinator.lan and coordinator.lan.connected),
            "lan_host": coordinator.lan.host if coordinator.lan else None,
            "cloud_connected": bool(coordinator.mqtt and coordinator.mqtt.connected),
        },
        "gateway": {
            "name": gateway.get("name"),
            "gwno": gateway.get("gwno"),
            "gwtype": gateway.get("gwtype"),
            "model": gateway.get("module") or gateway.get("gwname"),
            "mac": _mask_mac(gateway.get("mac")),
            "lan_ip": gateway.get("lanip") or gateway.get("lan_ip"),
            "firmware": gateway.get("softversion") or gateway.get("version"),
        },
        "platforms": {
            # 窗帘 / 场景 / 场景按键要等真机验证后才加载，这里的端点数不代表已有实体
            "cover_scene_enabled": ENABLE_COVER_SCENE,
            "loaded": ["light"] + (["cover", "scene", "button"] if ENABLE_COVER_SCENE else []),
        },
        "counts": {
            "devices": len(coordinator.devices),
            "lights": len(coordinator.endpoints),
            "covers": len(coordinator.covers),
            "scene_buttons": len(coordinator.scene_keys),
            "scenes": len(coordinator.scenes),
        },
        "device_types": dict(sorted(type_counts.items(), key=lambda kv: kv[0])),
        "unknown_types": unknown_types,
        "scenes": sorted(coordinator.scenes.values()),
        "entities": {
            "lights": [
                {
                    "name": e.name,
                    "room": e.room,
                    "device": e.device_name,
                    "dev_type": e.dev_type,
                    "index": e.index,
                }
                for e in coordinator.endpoints
            ],
            "covers": [
                {"name": e.name, "room": e.room, "device": e.device_name}
                for e in coordinator.covers
            ],
        },
    }
