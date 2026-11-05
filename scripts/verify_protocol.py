"""离线校验报文构造（不依赖 Home Assistant）。

对比对象是 App 1.7.2 的字节码证据：
  com/anjubao/smarthome/common/util/ActionUtil.controlDevice(bean, endpoints, msgId, subject)
  com/anjubao/smarthome/common/util/ActionUtil.controlDeviceStop(bean, index, serviceType, msgId, subject)
  com/anjubao/smarthome/ui/holder/CurtainHolder.onClick / controlDevice(value, index)
      iv_open_*  -> controlDevice(1, index)
      iv_close_* -> controlDevice(0, index)
      iv_stop_*  -> controlDevice(3, index)  → service/call, service_type=1, value=1

运行: python scripts/verify_protocol.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "anjubao"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


protocol = load("anjubao_protocol", PKG / "protocol.py")
lan = load("anjubao_lan", PKG / "lan.py")
devtypes = load("anjubao_devtypes", PKG / "devtypes.py")

MAC = "00158d000657e99f"
DEV_TYPE = 31

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print("  got :", json.dumps(got, ensure_ascii=False, sort_keys=True))
        print("  want:", json.dumps(want, ensure_ascii=False, sort_keys=True))
        failures.append(label)


# 1) 窗帘开 —— property_type 0, value 1
check(
    "curtain open -> device/property_set",
    protocol.build_property_set(MAC, DEV_TYPE, 1, {0: 1}),
    {
        "mac": MAC,
        "dev_type": 31,
        "endpoints": [{"index": 1, "properties": [{"property_type": 0, "value": 1}]}],
    },
)

# 2) 窗帘关 —— property_type 0, value 0
check(
    "curtain close -> device/property_set",
    protocol.build_property_set(MAC, DEV_TYPE, 2, {0: 0}),
    {
        "mac": MAC,
        "dev_type": 31,
        "endpoints": [{"index": 2, "properties": [{"property_type": 0, "value": 0}]}],
    },
)

# 3) 窗帘停 —— service/call, service_type 1, value 1
check(
    "curtain stop -> service/call",
    protocol.build_service_call(MAC, DEV_TYPE, 1, 1, 1),
    {
        "mac": MAC,
        "dev_type": 31,
        "endpoints": [{"index": 1, "service_type": 1, "value": 1}],
    },
)

# 4) 局域网侧：property_set 的云端写法要转成 App 原生 onoff
check(
    "lan normalize open -> onoff",
    lan.AnjubaoLan._normalize_control(protocol.build_property_set(MAC, DEV_TYPE, 1, {0: 1})),
    {"mac": MAC, "dev_type": 31, "endpoints": [{"index": 1, "onoff": 1}]},
)

# 5) 局域网侧：service/call 不带 properties，必须原样透传
check(
    "lan normalize stop -> untouched",
    lan.AnjubaoLan._normalize_control(protocol.build_service_call(MAC, DEV_TYPE, 1, 1, 1)),
    {
        "mac": MAC,
        "dev_type": 31,
        "endpoints": [{"index": 1, "service_type": 1, "value": 1}],
    },
)

# 6) 路由：控制类 subject 直接当路由，其余走 receive_message
print(f"{'PASS' if 'service/call' in lan.DIRECT_SUBJECT_ROUTES else 'FAIL'}  service/call 走直连路由")
print(f"{'PASS' if 'device/property_set' in lan.DIRECT_SUBJECT_ROUTES else 'FAIL'}  device/property_set 走直连路由")
print(f"{'PASS' if 'heartbeat' not in lan.DIRECT_SUBJECT_ROUTES else 'FAIL'}  heartbeat 走 receive_message")

# 7) 设备类型表（App 分发表 30 种 + 实测设备里遇到的 28）
check("设备类型表 31 种", len(devtypes.DEVICE_TYPES), 31)
check("31 -> 窗帘", devtypes.kind_of(31).category, "curtain")
check("10 -> 窗帘", devtypes.kind_of(10).category, "curtain")
check("34 -> 调光", devtypes.kind_of(34).category, "dimmer")
check("34 生成 light", devtypes.is_light(34), True)
check("16/39 插座生成 light", (devtypes.is_light(16), devtypes.is_light(39)), (True, True))
check("7/11/15/48 开关生成 light", all(devtypes.is_light(t) for t in (7, 11, 15, 48)), True)
check("28 场景面板不生成实体", devtypes.is_light(28), False)
check("82~91 面板不生成实体", any(devtypes.is_light(t) for t in range(82, 92)), False)
check("3 报警不生成实体", devtypes.is_light(3), False)
check("37/101 新风不生成实体", (devtypes.is_light(37), devtypes.is_light(101)), (False, False))
check("未收录类型按开关兜底", devtypes.is_light(200), True)

# 8) 场景报文
check("scene/set 报文体", protocol.build_scene_set("gohome_abc"), {"scene_uuid": "gohome_abc"})
check("scene/set 走直连路由", "scene/set" in lan.DIRECT_SUBJECT_ROUTES, True)
check("scene/sync 走直连路由", "scene/sync" in lan.DIRECT_SUBJECT_ROUTES, True)
check("scene/sync 不被端点转换改动", lan.AnjubaoLan._normalize_control({"subject": "scene/sync", "msg_id": 1}),
      {"subject": "scene/sync", "msg_id": 1})

print()
if failures:
    print(f"{len(failures)} 项不通过")
    raise SystemExit(1)
print("全部通过")
