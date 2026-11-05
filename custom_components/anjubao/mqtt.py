"""安居家园 MQTT 通道。

服务端约束（实测）：
  - 必须用 App 内置客户端证书做 mTLS（TLS 1.3 下不带证书会在首条应用数据后断开）
  - clientId 必须等于 userId，换成其他值服务端静默断开
  - password 是 appauth 返回的一次性票据，每次连接前都要重新换取
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import ssl
import threading
import time
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .api import AnjubaoError
from .const import TOPIC_PUB, TOPIC_SUB

_LOGGER = logging.getLogger(__name__)

CERT_DIR = os.path.join(os.path.dirname(__file__), "certs")
CERT_FILE = os.path.join(CERT_DIR, "mqttclient.crt")
KEY_FILE = os.path.join(CERT_DIR, "mqttclient.key")

# 用户可在 HA 配置目录下放置自己的证书来覆盖内置文件，见 README「自定义证书」
CERT_NAMES = ("mqttclient.crt", "mqttclient.key")


def _make_client(client_id: str) -> mqtt.Client:
    try:  # paho-mqtt 2.x
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
    except (AttributeError, TypeError):  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id, clean_session=True)


def _cert_paths(cert_dir: str | None = None) -> tuple[str, str]:
    """返回实际使用的 (证书, 私钥)。

    `cert_dir` 下若同时存在两个文件则优先使用，否则回退到内置的 certs/。
    这样用户可以用自己从 App 安装包提取的证书替换内置文件。
    """
    if cert_dir:
        cert = os.path.join(cert_dir, CERT_NAMES[0])
        key = os.path.join(cert_dir, CERT_NAMES[1])
        if os.path.isfile(cert) and os.path.isfile(key):
            _LOGGER.debug("使用自定义客户端证书：%s", cert_dir)
            return cert, key
    return CERT_FILE, KEY_FILE


def _ssl_context(cert_dir: str | None = None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")  # 厂商证书是 1024 位 RSA
    except ssl.SSLError:  # pragma: no cover
        pass
    ctx.load_cert_chain(*_cert_paths(cert_dir))
    return ctx


class AnjubaoMqtt:
    """后台线程维持一条长连接，提供阻塞式 request()。"""

    def __init__(
        self,
        api: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        on_report: Callable[[str, int, int, Any], None],
        on_connection: Callable[[bool], None] | None = None,
        cert_dir: str | None = None,
    ) -> None:
        self._api = api
        self._cert_dir = cert_dir
        self._loop = loop
        self._on_report = on_report
        self._on_connection = on_connection
        self._client: mqtt.Client | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._waiters: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._waiters_lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._gateway: tuple[int, str] | None = None
        self._home_id: str = ""
        self._user_id: str = ""

    # ------------------------------------------------------------------ 生命周期
    def start(self, user_id: str, home_id: str, gateway: tuple[int, str]) -> None:
        self._user_id = user_id
        self._home_id = home_id
        self._gateway = gateway
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="anjubao-mqtt", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        client = self._client
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._connected.clear()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------ 工作线程
    def _worker(self) -> None:
        backoff = 5
        while not self._stop.is_set():
            try:
                address = self._run_coro(self._api.async_appauth(), timeout=25)
                host = address.get("host", "218.107.12.83")
                port = int(address.get("port", 1883))
                ticket = address["secretkey"]

                client = _make_client(self._user_id)
                client.username_pw_set(self._user_id, ticket)
                client.tls_set_context(_ssl_context(getattr(self, "_cert_dir", None)))
                client.tls_insecure_set(True)
                client.on_connect = self._on_connect
                client.on_disconnect = self._on_disconnect
                client.on_message = self._on_message
                client.reconnect_delay_set(min_delay=2, max_delay=30)
                self._client = client

                client.connect(host, port, keepalive=30)
                client.loop_start()

                if not self._connected.wait(timeout=15):
                    raise AnjubaoError("CONNECT 未收到 CONNACK")

                backoff = 5
                _LOGGER.info("安居家园 MQTT 已连接 %s:%s", host, port)
                while not self._stop.is_set() and self._connected.is_set():
                    time.sleep(1)
            except Exception as err:  # noqa: BLE001
                if not self._stop.is_set():
                    _LOGGER.warning("安居家园 MQTT 断开: %s（%s 秒后重连）", err, backoff)
            finally:
                self._connected.clear()
                self._callback(self._on_connection, False)
                self._teardown_client()
                self._fail_waiters()
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _teardown_client(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass

    def _run_coro(self, coro, timeout: float = 20):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def _callback(self, func, *args) -> None:
        if func is None:
            return
        try:
            self._loop.call_soon_threadsafe(func, *args)
        except RuntimeError:  # pragma: no cover - 事件循环已关闭
            pass

    # ------------------------------------------------------------------ paho 回调
    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        rc = getattr(reason_code, "value", reason_code)
        if rc == 0:
            client.subscribe(TOPIC_SUB.format(user_id=self._user_id), qos=1)
            self._connected.set()
            self._callback(self._on_connection, True)
        else:
            _LOGGER.error("安居家园 MQTT CONNACK 失败 rc=%s", rc)
            self._connected.clear()

    def _on_disconnect(self, client, userdata, *args) -> None:
        self._connected.clear()

    def _on_message(self, client, userdata, message) -> None:
        try:
            data = json.loads(message.payload.decode("utf-8", "ignore"))
        except ValueError:
            return
        msg_id = data.get("msg_id")
        if isinstance(msg_id, int):
            with self._waiters_lock:
                waiter = self._waiters.pop(msg_id, None)
            if waiter is not None:
                event, holder = waiter
                holder["data"] = data
                event.set()
        if data.get("subject") == "device/property_report":
            mac = data.get("mac")
            index = data.get("index")
            if isinstance(mac, str) and isinstance(index, int):
                self._callback(
                    self._on_report,
                    mac,
                    index,
                    int(data.get("property_type", 0)),
                    data.get("value"),
                )

    # ------------------------------------------------------------------ 发送
    def _fail_waiters(self) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters.values())
            self._waiters.clear()
        for event, holder in waiters:
            holder["error"] = "连接已断开"
            event.set()

    def _publish(self, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None or not self._connected.is_set():
            raise AnjubaoError("MQTT 未连接")
        with self._publish_lock:
            info = client.publish(
                TOPIC_PUB.format(user_id=self._user_id),
                json.dumps(payload, ensure_ascii=False),
                qos=1,
            )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise AnjubaoError(f"发布失败 rc={info.rc}")

    def request(
        self,
        subject: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        """发一条业务指令并等待同 msg_id 的回复（阻塞，需在线程池里调用）。"""
        if self._gateway is None:
            raise AnjubaoError("网关信息未初始化")
        gwtype, gwno = self._gateway
        msg_id = random.randint(1, 999_999)
        inner: dict[str, Any] = {"subject": subject, "msg_id": msg_id, "homeid": self._home_id}
        if body:
            inner.update(body)
        outer = {
            "msgid": msg_id,
            "topic": f"/cloud/{gwtype}/{gwno}/receive_message",
            "payload": inner,
        }
        event = threading.Event()
        holder: dict[str, Any] = {}
        with self._waiters_lock:
            self._waiters[msg_id] = (event, holder)
        try:
            self._publish(outer)
            if not event.wait(timeout):
                raise AnjubaoError(f"{subject} 超时未响应")
        finally:
            with self._waiters_lock:
                self._waiters.pop(msg_id, None)
        if "error" in holder:
            raise AnjubaoError(holder["error"])
        return holder.get("data", {})

    def fire(self, subject: str, body: dict[str, Any] | None = None) -> None:
        """只发不等回复。"""
        if self._gateway is None:
            raise AnjubaoError("网关信息未初始化")
        gwtype, gwno = self._gateway
        msg_id = random.randint(1, 999_999)
        inner: dict[str, Any] = {"subject": subject, "msg_id": msg_id, "homeid": self._home_id}
        if body:
            inner.update(body)
        self._publish(
            {
                "msgid": msg_id,
                "topic": f"/cloud/{gwtype}/{gwno}/receive_message",
                "payload": inner,
            }
        )
