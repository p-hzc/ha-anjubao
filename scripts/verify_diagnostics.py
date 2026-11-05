"""离线冒烟测试 diagnostics，确保「下载诊断信息」不会因属性缺失崩掉。

不依赖 Home Assistant 运行时：stub 掉 homeassistant 模块后，
用真实的 devicelistproperty/get 与 scene/sync 数据构造一个假 coordinator。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "anjubao"
# 本地抓包样本目录，仅作者本机存在（已列入 .gitignore，不随仓库分发）。
# 里面是作者自有设备的报文，含真实房间名与网关标识，因此不进版本库。
# 跑本脚本需要该目录；可用环境变量 ANJUBAO_SAMPLE_DIR 指向自己的样本。
DATA = Path(os.environ.get("ANJUBAO_SAMPLE_DIR", "C:/word/anju_re"))


def stub_homeassistant() -> None:
    ha = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401
        pass

    core.HomeAssistant = HomeAssistant

    ce = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:  # noqa: D401
        pass

    ce.ConfigEntry = ConfigEntry

    helpers = types.ModuleType("homeassistant.helpers")
    uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __class_getitem__(cls, item):
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
            "homeassistant.config_entries": ce,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.update_coordinator": uc,
        }
    )


class FakeChannel:
    def __init__(self, connected: bool, host: str | None = None) -> None:
        self.connected = connected
        self.host = host


class FakeApi:
    def __init__(self) -> None:
        self.access_token = "eyJhbGciOiJIUzI1NiJ9.fake.token"
        self.refresh_token = "def50200fake"


def main() -> int:
    stub_homeassistant()
    pkg = types.ModuleType("cc")
    pkg.__path__ = [str(PKG)]
    sys.modules["cc"] = pkg

    from cc import coordinator as co
    from cc import diagnostics as diag

    devices = json.load(
        open(DATA / "reply_devicelistproperty_get.json", encoding="utf-8")
    )["devicelist"]
    scenes_reply = json.load(open(DATA / "reply_scene_sync.json", encoding="utf-8"))
    scenes = {
        s["scene_uuid"]: s["scene_name"]
        for s in scenes_reply["scene_list"]
        if s.get("scene_uuid")
    }

    coord = object.__new__(co.AnjubaoCoordinator)
    coord.devices = devices
    coord.scenes = scenes
    coord.endpoints = []
    coord.covers = []
    coord.scene_keys = []
    coord._build_endpoints()
    coord.gateway = {
        "name": "AJB-G4",
        "gwno": "3B9AE03F",
        "gwtype": 35,
        "mac": "00158d00080caf49",
        "softversion": "AJB.N.8.4057.20250910",
        "lanip": "192.168.1.18",
    }
    coord.api = FakeApi()
    coord.lan = FakeChannel(True, "192.168.1.18")
    coord.mqtt = None
    coord._transport = co.TRANSPORT_LAN

    entry = types.SimpleNamespace(
        entry_id="abc123",
        unique_id="4028e508923bd5700192ebc819403885",
        version=1,
        data={
            "username": "13800138000",
            "password": "super-secret-password",
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
        },
        options={"scan_interval": 60, "lan": True, "lan_host": ""},
    )
    hass = types.SimpleNamespace(data={"anjubao": {"abc123": coord}})

    out = asyncio.run(diag.async_get_config_entry_diagnostics(hass, entry))

    text = json.dumps(out, ensure_ascii=False, indent=1)
    print(text[:1400])
    print("...")

    checks = [
        ("账号已打码", out["account"] == "138****8000"),
        ("密码不外泄", "super-secret-password" not in text),
        ("token 不外泄", "secret-access" not in text and "secret-refresh" not in text),
        ("accessToken 不落盘", "eyJhbGciOiJIUzI1NiJ9" not in text),
        ("网关 MAC 已打码", out["gateway"]["mac"].startswith("*")),
        ("通道为 lan", out["transport"]["active"] == "lan"),
        ("局域网在线", out["transport"]["lan_connected"] is True),
        ("27 个 light", out["counts"]["lights"] == 27),
        ("2 个 cover", out["counts"]["covers"] == 2),
        ("4 个场景按键", out["counts"]["scene_buttons"] == 4),
        ("9 个场景", out["counts"]["scenes"] == 9),
        ("类型分布非空", len(out["device_types"]) > 0),
        ("未收录类型为空", out["unknown_types"] == []),
    ]
    print()
    ok = True
    for label, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and bool(passed)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
