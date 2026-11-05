"""校验 setup 失败时不会泄漏线程 / MQTT 连接（HACS 审核意见第 4 条）。

场景：coordinator.async_setup() 已经把局域网线程与 MQTT 客户端拉起来，
随后抛错（例如「局域网与厂商云均无法连接」）。此时若只 raise
ConfigEntryNotReady 而不 stop，HA 每次重试都会多留一套线程，且
clientId 相同会让新旧客户端互相顶下线。

Run:  python scripts/verify_setup_cleanup.py
"""

from __future__ import annotations

import asyncio
import enum
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "anjubao"


def stub_modules() -> None:
    # --- voluptuous ---
    vol = types.ModuleType("voluptuous")

    class _Marker:
        def __init__(self, *args, **kwargs):
            pass

        def __hash__(self):
            return id(self)

    vol.Schema = lambda *a, **k: _Marker()
    vol.Required = _Marker
    vol.Optional = _Marker
    vol.All = _Marker
    vol.Range = _Marker
    vol.In = _Marker
    sys.modules["voluptuous"] = vol

    # --- homeassistant ---
    ha = types.ModuleType("homeassistant")
    ce = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:  # noqa: D401
        pass

    ce.ConfigEntry = ConfigEntry

    const = types.ModuleType("homeassistant.const")

    class Platform(enum.Enum):
        LIGHT = "light"
        COVER = "cover"
        SCENE = "scene"
        BUTTON = "button"

    const.Platform = Platform

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.ServiceCall = type("ServiceCall", (), {})

    exc = types.ModuleType("homeassistant.exceptions")

    class ConfigEntryNotReady(Exception):
        pass

    class HomeAssistantError(Exception):
        pass

    exc.ConfigEntryNotReady = ConfigEntryNotReady
    exc.HomeAssistantError = HomeAssistantError

    helpers = types.ModuleType("homeassistant.helpers")
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    cv.string = str
    cv.boolean = bool
    uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, item):
            return cls

    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.UpdateFailed = type("UpdateFailed", (Exception,), {})
    helpers.update_coordinator = uc

    aio = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aio.async_get_clientsession = lambda hass: None

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.config_entries": ce,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.exceptions": exc,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": cv,
            "homeassistant.helpers.update_coordinator": uc,
            "homeassistant.helpers.aiohttp_client": aio,
        }
    )

    # --- paho（coordinator 会间接导入 mqtt）---
    for name in ("paho", "paho.mqtt", "paho.mqtt.client"):
        sys.modules[name] = types.ModuleType(name)
    cm = sys.modules["paho.mqtt.client"]
    cm.Client = object
    cm.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    sys.modules["paho.mqtt"].client = cm


def load_integration():
    stub_modules()
    pkg = types.ModuleType("anjubao")
    pkg.__path__ = [str(PKG)]
    sys.modules["anjubao"] = pkg

    import importlib.util

    spec = importlib.util.spec_from_file_location("anjubao.__init__", PKG / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["anjubao.__init__"] = module
    spec.loader.exec_module(module)
    return module


class FakeHass:
    def __init__(self):
        self.data = {}

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeEntry:
    entry_id = "test-entry"
    data = {"username": "u", "password": "p"}
    options = {}

    def async_on_unload(self, _):
        pass

    def add_update_listener(self, _):
        return lambda: None


def make_coordinator(cls, error_cls):
    """返回一个 async_setup 抛错的 coordinator，并记录是否被 stop。"""

    class FakeCoordinator:
        instances: list["FakeCoordinator"] = []

        def __init__(self, hass, api, data):
            self.stopped = 0
            self.hass = hass
            FakeCoordinator.instances.append(self)

        async def async_setup(self):
            raise error_cls("局域网与厂商云均无法连接")

        def stop(self):
            self.stopped += 1

    return FakeCoordinator


def main() -> int:
    integration = load_integration()
    anjubao_error = sys.modules["anjubao.api"].AnjubaoError
    fake_cls = make_coordinator(integration, anjubao_error)
    integration.AnjubaoCoordinator = fake_cls

    hass = FakeHass()
    entry = FakeEntry()

    checks = []
    try:
        asyncio.run(integration.async_setup_entry(hass, entry))
        checks.append(("setup 失败时抛出异常", False))
        error = None
    except Exception as err:  # noqa: BLE001
        error = err
        checks.append(("setup 失败时抛出异常", True))

    instance = fake_cls.instances[-1] if fake_cls.instances else None
    checks.append(("setup 失败后调用了 coordinator.stop", bool(instance and instance.stopped == 1)))
    checks.append(("AnjubaoError 被转成 ConfigEntryNotReady",
                   type(error).__name__ == "ConfigEntryNotReady"))
    checks.append(("失败后没有把 coordinator 写进 hass.data",
                   entry.entry_id not in (hass.data.get("anjubao") or {})))

    ok = True
    for label, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and bool(passed)
    if error is not None:
        print(f"\n（异常类型：{type(error).__name__}: {error}）")
    print("\n全部通过" if ok else "\n存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
