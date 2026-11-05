"""校验「自定义证书」的回退逻辑（README「自定义证书」一节）。

不依赖 Home Assistant 与 paho 运行时：stub 掉外部模块后单独加载 mqtt.py。

Run:  python scripts/verify_certs.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "anjubao"


def load_mqtt() -> types.ModuleType:
    """在隔离环境里加载 mqtt.py，避免拖入 homeassistant / paho。"""
    for name in ("paho", "paho.mqtt", "paho.mqtt.client"):
        sys.modules[name] = types.ModuleType(name)
    client_mod = sys.modules["paho.mqtt.client"]
    client_mod.Client = object
    client_mod.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    sys.modules["paho.mqtt"].client = client_mod

    pkg = types.ModuleType("anjubao")
    pkg.__path__ = [str(PKG)]
    sys.modules["anjubao"] = pkg

    api_mod = types.ModuleType("anjubao.api")
    api_mod.AnjubaoError = type("AnjubaoError", (Exception,), {})
    sys.modules["anjubao.api"] = api_mod

    for mod_name, filename in (("anjubao.const", "const.py"), ("anjubao.mqtt", "mqtt.py")):
        spec = importlib.util.spec_from_file_location(mod_name, PKG / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)

    return sys.modules["anjubao.mqtt"]


def main() -> int:
    mqtt = load_mqtt()

    cert, key = mqtt._cert_paths(None)
    checks = [
        ("默认用内置证书", cert == mqtt.CERT_FILE and key == mqtt.KEY_FILE),
        ("内置证书文件存在", os.path.isfile(mqtt.CERT_FILE) and os.path.isfile(mqtt.KEY_FILE)),
    ]

    empty = tempfile.mkdtemp()
    checks.append(("空目录回退内置", mqtt._cert_paths(empty)[0] == mqtt.CERT_FILE))

    partial = tempfile.mkdtemp()
    Path(partial, "mqttclient.crt").write_text("x", encoding="utf-8")
    checks.append(("只有 crt 时回退内置", mqtt._cert_paths(partial)[0] == mqtt.CERT_FILE))

    custom = tempfile.mkdtemp()
    Path(custom, "mqttclient.crt").write_text("x", encoding="utf-8")
    Path(custom, "mqttclient.key").write_text("y", encoding="utf-8")
    checks.append(("两个都在时用自定义", mqtt._cert_paths(custom)[0].startswith(custom)))
    checks.append(("内置证书能建 TLS 上下文", mqtt._ssl_context(None) is not None))

    ok = True
    for label, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and bool(passed)
    print("\n全部通过" if ok else "\n存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
