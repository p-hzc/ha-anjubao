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
import hashlib
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

# 厂商 MQTT 服务端的自签证书（CN=AJB，1024 位 RSA，2020-2120），作为唯一信任锚
SERVER_CERT_FILE = os.path.join(CERT_DIR, "mqttserver.crt")
# 上面那张证书的 SHA-256 指纹（DER）。服务端是按 IP 访问的、证书里也没有 SAN，
# 没法做主机名校验，所以改成固定指纹：握手后比对，不一致立刻断开。
SERVER_CERT_PIN = "c61a428e75cdf86fb0cd7afab3d0b844a984e4b6bcb36b54627829914c09598e"

# 用户可在 HA 配置目录下放置自己的证书来覆盖内置文件，见 README「自定义证书」
CERT_NAMES = ("mqttclient.crt", "mqttclient.key")


class CertError(AnjubaoError):
    """服务器证书与内置指纹不符。重试无意义，应当停下等人处理。"""


class _PinnedContext(ssl.SSLContext):
    """在 TLS 握手完成后校验服务器证书指纹。

    cafile 只保证「证书由厂商那张自签证书签发」，指纹则保证「就是那一张」，
    两者都过才允许上层发数据，避免中间人替换证书后读到票据与全部指令。
    """

    def wrap_socket(  # type: ignore[override]
        self,
        sock,
        server_side=False,
        do_handshake_on_connect=True,
        suppress_ragged_eofs=True,
        server_hostname=None,
        session=None,
    ):
        sslsock = super().wrap_socket(
            sock,
            server_side=server_side,
            do_handshake_on_connect=do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=server_hostname,
            session=session,
        )
        if server_side:
            return sslsock
        # paho 1.6.1 用 do_handshake_on_connect=False 包装 socket，包装完才自己握手。
        # 握手没完成时 getpeercert() 会抛 ValueError("handshake not done yet")，
        # 而 paho 把 ValueError 当成「该 Python 不支持 SNI」，拿同一个已被 ssl 模块
        # detach 掉的 socket 再 wrap 一次，于是变成 OSError(9, Bad file descriptor)。
        # 旧实现正是这样卡在 EBADF 无限重连里，所以这里先补握手再取证书。
        der = b""
        try:
            der = sslsock.getpeercert(binary_form=True) or b""
        except ValueError:
            der = b""
        if not der:
            try:
                sslsock.do_handshake()
            except (ssl.SSLError, OSError):
                sslsock.close()
                raise
            der = sslsock.getpeercert(binary_form=True) or b""
        digest = hashlib.sha256(der).hexdigest()
        if digest != SERVER_CERT_PIN:
            sslsock.close()
            raise CertError(
                f"MQTT 服务器证书指纹不符（收到 {digest}，预期 {SERVER_CERT_PIN}）。"
                "厂商可能更换了证书，或连接正被中间人劫持，已中止连接。"
            )
        return sslsock


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
    """构建强制校验服务端的 TLS 上下文（mTLS 客户端证书 + 服务器证书钉扎）。"""
    ctx = _PinnedContext(ssl.PROTOCOL_TLS_CLIENT)
    # 服务端按 IP 访问、证书无 SAN，无法做主机名校验；
    # 用「cafile 信任锚 + CERT_REQUIRED + 握手后比对指纹」替代，同样能挡住中间人。
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=SERVER_CERT_FILE)
    try:
        # 厂商证书是 1024 位 RSA，OpenSSL 3 默认 SECLEVEL=2 会直接判为 key too weak
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
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
            except (CertError, ssl.SSLCertVerificationError) as err:
                # 证书问题重试多少次都一样，继续重连只会刷日志、反复顶掉手机 App
                _LOGGER.error(
                    "安居家园 MQTT 服务器证书校验失败，已停止重连：%s。"
                    "若厂商确实更换了证书，需要更新集成版本内置的证书与指纹。",
                    err,
                )
                break
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
