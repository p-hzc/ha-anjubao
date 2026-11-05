"""报文构造（纯函数，不依赖 Home Assistant，方便离线校验）。

两种报文都来自 App 1.7.2 的反编译实证：

1) ActionUtil.controlDevice(...)  —— 开关/调光/窗帘开合
   {"subject":"device/property_set","msg_id":N,"mac":..,"dev_type":..,
    "endpoints":[{"index":i,"properties":[{"property_type":t,"value":v}]}]}

2) ActionUtil.controlDeviceStop(...) —— 窗帘停止（App 里固定 service_type=1, value=1）
   {"subject":"service/call","msg_id":N,"mac":..,"dev_type":..,
    "endpoints":[{"index":i,"service_type":1,"value":1}]}

3) ActionUtil.intoScene(uid, msgId, "scene/set") —— 执行场景（场景列表、场景面板按键都走这个）
   {"subject":"scene/set","msg_id":N,"scene_uuid":"gohome_xxx"}

4) ActionUtil.normalAction(msgId, "scene/sync") —— 拉取场景列表
   {"subject":"scene/sync","msg_id":N}  ->  回复 {"subject":"scene/sync_reply","scene_list":[...]}
"""
from __future__ import annotations

from typing import Any


def build_property_set(
    mac: str, dev_type: int, index: int, properties: dict[int, Any]
) -> dict[str, Any]:
    """device/property_set 报文体。"""
    return {
        "mac": mac,
        "dev_type": int(dev_type),
        "endpoints": [
            {
                "index": int(index),
                "properties": [
                    {"property_type": int(prop), "value": value}
                    for prop, value in properties.items()
                ],
            }
        ],
    }


def build_service_call(
    mac: str, dev_type: int, index: int, service_type: int, value: int
) -> dict[str, Any]:
    """service/call 报文体（窗帘停止用 service_type=1、value=1）。"""
    return {
        "mac": mac,
        "dev_type": int(dev_type),
        "endpoints": [
            {
                "index": int(index),
                "service_type": int(service_type),
                "value": int(value),
            }
        ],
    }


def build_scene_set(scene_uuid: str) -> dict[str, Any]:
    """scene/set 报文体：执行一个场景。"""
    return {"scene_uuid": scene_uuid}
