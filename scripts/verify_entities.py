"""用真实网关数据验证实体分类（不依赖 Home Assistant 运行时）。

数据来源：
  anju_re/reply_devicelistproperty_get.json  —— devicelistproperty/get 实际返回
  anju_re/reply_scene_sync.json              —— scene/sync 实际返回
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "anjubao"


def stub_homeassistant() -> None:
    ha = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401
        pass

    core.HomeAssistant = HomeAssistant

    helpers = types.ModuleType("homeassistant.helpers")
    uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __class_getitem__(cls, item):  # 让 DataUpdateCoordinator[T] 可用
            return cls

    class UpdateFailed(Exception):
        pass

    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.UpdateFailed = UpdateFailed
    helpers.update_coordinator = uc

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.update_coordinator": uc,
        }
    )


def main() -> int:
    stub_homeassistant()
    pkg = types.ModuleType("cc")
    pkg.__path__ = [str(PKG)]
    sys.modules["cc"] = pkg

    from cc import coordinator as co
    from cc.const import ENABLE_COVER_SCENE

    devices = json.load(
        open("C:/word/anju_re/reply_devicelistproperty_get.json", encoding="utf-8")
    )["devicelist"]
    scenes_reply = json.load(open("C:/word/anju_re/reply_scene_sync.json", encoding="utf-8"))

    scenes = {
        s["scene_uuid"]: s["scene_name"]
        for s in scenes_reply["scene_list"]
        if s.get("scene_uuid")
    }

    fake = object.__new__(co.AnjubaoCoordinator)
    fake.devices = devices
    fake.scenes = scenes
    fake.endpoints = []
    fake.covers = []
    fake.scene_keys = []

    fake._build_endpoints()

    print(f"设备 {len(devices)} 个")
    print(f"light 实体   {len(fake.endpoints)}")
    print(f"cover 实体   {len(fake.covers)}")
    print(f"场景按键     {len(fake.scene_keys)}")
    print(f"场景实体     {len(scenes)}")

    print("\n--- 场景按键 ---")
    for ep in fake.scene_keys:
        print(f"  {ep.device_name} / {ep.name} -> {scenes.get(ep.scene_uuid, '?')}  ({ep.scene_uuid})")

    print("\n--- 场景实体 ---")
    for uuid, name in scenes.items():
        print(f"  {name}  ({uuid})")

    print("\n--- 窗帘 ---")
    for ep in fake.covers:
        print(f"  {ep.device_name} / {ep.name} (type {ep.dev_type})")

    print("\n--- 灯回路前 5 个 ---")
    for ep in fake.endpoints[:5]:
        print(f"  {ep.device_name} / {ep.name} (type {ep.dev_type})")

    checks = [
        ("4 个场景按键", len(fake.scene_keys) == 4),
        ("场景按键都绑了场景", all(ep.scene_uuid in scenes for ep in fake.scene_keys)),
        ("9 个场景", len(scenes) == 9),
        ("2 个窗帘", len(fake.covers) == 2),
        ("27 个灯回路", len(fake.endpoints) == 27),
        ("场景按键不再进 light", all(not ep.is_scene_key for ep in fake.endpoints)),
        ("cover/scene 平台未启用（只发灯）", ENABLE_COVER_SCENE is False),
    ]
    print()
    ok = True
    for label, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
