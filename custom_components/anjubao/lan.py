"""安居家园局域网直连通道（UDP 发现 + TCP 长连接）。

完全按 App 1.7.2 的实际行为实现，协议来自反编译 + 字节码逐条核对：

  发现（UDP）
    发送到 255.255.255.255:9000，本机绑定 9000 接收：
      PACKLEN: + 4 字节大端长度 + payload
      payload = "/lan/gateway/scan\r\n" + '{"msg_id":1}'
    网关回 /lan/gateway/scan_reply + JSON（含 gwid / ip_wlan0 / ip_eth0 / port）

  连接（TCP，默认端口 8000）
    网关只认 PACKLEN 分帧，帧长 = 4 字节长度值 + 12（"PACKLEN:" 8 + 长度 4）

  路由形态（真机实测，固件 AJB.N.8.4057.20250910）
    **所有业务指令都走同一条路由**，subject 放在 JSON 里：
      /lan/{gwtype}/{gwno}/receive_message\r\n{"subject":...,"msg_id":...,"homeid":...}

    早期文档据反编译认为「控制类指令把 subject 当路由」（/lan/../device/property_set），
    实测这条路由网关既不回复也不执行，已废弃，见下方 DIRECT_SUBJECT_ROUTES 注释。

  回复
    /lan/{gwtype}/{gwno}/receive_reply\r\n{json}   对 receive_message 的回复
    /lan/{gwtype}/{gwno}/send_message\r\n{json}    网关主动上报（device/property_report）

  心跳：每 5 秒一次，subject=heartbeat，bind_sta=1，走 receive_message
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import struct
import threading
import time
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

UDP_PORT = 9000
DEFAULT_TCP_PORT = 8000
SCAN_INTERVAL = 3.0
HEARTBEAT_INTERVAL = 5.0
FRAME_MAGIC = b"PACKLEN:"
HEADER_LEN = 12  # "PACKLEN:"(8) + 长度(4)

ROUTE_RECEIVE = "/receive_message"

# 真机实测（AJB.N.8.4057.20250910）结论：
#   把 subject 直接当路由，即 /lan/{gwtype}/{gwno}/device/property_set\r\n{...}，
#   网关既不回包也不执行（开关状态回读仍是旧值）。
#   同一条指令改成 /receive_message + JSON 里的 subject，立刻收到
#   {"subject":"device/property_set_reply","code":0} 并推送 device/property_report。
# scene/sync、scene/set 同理。
# 所以这里不像早期反编译结论那样区分路由，一律走 /receive_message。
DIRECT_SUBJECT_ROUTES: set[str] = set()


class LanError(Exception):
    """局域网通道异常。"""


def _pack(payload: bytes) -> bytes:
    """按网关协议加 PACKLEN 头。"""
    return FRAME_MAGIC + struct.pack(">I", len(payload)) + payload


def extract_json(text: str) -> dict[str, Any] | None:
    """从帧内容里取出第一个能解析的 JSON 对象（与 App 的 extractMessage 等价）。

    PACKLEN 头里有 4 字节二进制长度，可能恰好是 '{'，
    所以第一个花括号解析失败时要继续往后找。
    """
    start = text.find("{")
    while start >= 0:
        parsed = _scan_object(text, start)
        if parsed is not None:
            return parsed
        start = text.find("{", start + 1)
    return None


def _scan_object(text: str, start: int) -> dict[str, Any] | None:
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except ValueError:
                    return None
    return None


class AnjubaoLan:
    """一条到网关的 TCP 长连接，后台线程维护，对外提供阻塞式 request()。"""

    def __init__(
        self,
        api: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        on_report: Callable[[str, int, int, Any], None],
        on_connection: Callable[[bool], None] | None = None,
        host: str | None = None,
        port: int | None = None,
        scan_target: tuple[str, int] | None = None,
    ) -> None:
        self._api = api
        self._loop = loop
        self._on_report = on_report
        self._on_connection = on_connection
        self._host = host or None
        self._port = int(port) if port else DEFAULT_TCP_PORT
        self._scan_target = scan_target or ("255.255.255.255", UDP_PORT)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._sock: socket.socket | None = None
        self._home_id = ""
        self._gwtype = 0
        self._gwno = ""
        self._user_id = ""

        self._waiters: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._waiters_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._last_rx = 0.0

    # ------------------------------------------------------------------ 生命周期
    def start(self, user_id: str, home_id: str, gateway: tuple[int, str]) -> None:
        self._user_id = user_id
        self._home_id = home_id
        self._gwtype, self._gwno = gateway
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="anjubao-lan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._connected.clear()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def host(self) -> str | None:
        """当前连接（或指定）的网关地址，诊断信息用。"""
        return self._host

    def _close(self) -> None:
        sock = self._sock
        self._sock = None
        self._connected.clear()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ 工作线程
    def _worker(self) -> None:
        backoff = 3
        while not self._stop.is_set():
            try:
                target = self._resolve_target()
                _LOGGER.debug("安居家园局域网连接 %s:%s", target[0], target[1])
                sock = socket.create_connection(target, timeout=6)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(1.0)
                self._sock = sock
                self._last_rx = time.time()
                self._connected.set()
                self._callback(self._on_connection, True)
                _LOGGER.info("安居家园局域网已连接 %s:%s", target[0], target[1])

                backoff = 3
                self._read_loop(sock)
            except Exception as err:  # noqa: BLE001
                if not self._stop.is_set():
                    _LOGGER.debug("安居家园局域网通道断开: %s（%s 秒后重试）", err, backoff)
            finally:
                was_connected = self._connected.is_set()
                self._close()
                self._fail_waiters()
                if was_connected:
                    self._callback(self._on_connection, False)
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _resolve_target(self) -> tuple[str, int]:
        """确定网关地址：优先手工配置，其次 UDP 广播发现，最后用云端记录的局域网 IP。"""
        if self._host:
            return (self._host, self._port)

        found = self._udp_discover()
        if found:
            return found

        # 云端 gatewayInfo 里带 ipwlan0 / ipeth0，作为兜底
        gateway = getattr(self._api, "gateway", None) or {}
        for key in ("ipwlan0", "ipwlan_0", "ipeth0", "ip"):
            ip = gateway.get(key)
            if isinstance(ip, str) and len(ip) > 6:
                return (ip, self._port)
        raise LanError("未发现局域网网关")

    def _udp_discover(self) -> tuple[str, int] | None:
        """广播 /lan/gateway/scan，等待 scan_reply。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", UDP_PORT))
        except OSError as err:
            _LOGGER.debug("UDP 9000 绑定失败（可能被占用）: %s", err)
        sock.settimeout(0.5)

        payload = b'/lan/gateway/scan\r\n{"msg_id":1}'
        frame = _pack(payload)
        target = self._scan_target
        deadline = time.time() + SCAN_INTERVAL
        try:
            while time.time() < deadline and not self._stop.is_set():
                try:
                    sock.sendto(frame, target)
                except OSError as err:
                    _LOGGER.debug("UDP 广播失败: %s", err)
                end = time.time() + 0.6
                while time.time() < end and not self._stop.is_set():
                    try:
                        data, addr = sock.recvfrom(4096)
                    except socket.timeout:
                        break
                    except OSError:
                        break
                    hit = self._parse_scan_reply(data, addr)
                    if hit:
                        _LOGGER.info(
                            "安居家园局域网发现网关 %s:%s（gwid=%s）", hit[0], hit[1], self._gwno
                        )
                        return hit
        finally:
            sock.close()
        return None

    def _parse_scan_reply(self, data: bytes, addr: tuple[str, int]) -> tuple[str, int] | None:
        text = data.decode("utf-8", "ignore")
        info = extract_json(text)
        if not info:
            return None
        gwid = str(info.get("gwid") or "")
        if info.get("type") not in (0, None) and gwid == "":
            return None
        # 只接受自己家网关：gwid 与 gwno 相同，或 gwno 出现在 gwid/mac 里
        if self._gwno and self._gwno not in gwid and self._gwno not in str(info.get("mac_wlan0") or ""):
            _LOGGER.debug("忽略其它网关广播 gwid=%s", gwid)
            return None
        ip = str(info.get("ip_wlan0") or "")
        if len(ip) <= 6:
            ip = str(info.get("ip_eth0") or "")
        if len(ip) <= 6:
            ip = addr[0]
        port = info.get("port")
        port = int(port) if isinstance(port, int) and port > 0 else DEFAULT_TCP_PORT
        return (ip, port)

    # ------------------------------------------------------------------ 收
    def _read_loop(self, sock: socket.socket) -> None:
        buf = bytearray()
        last_beat = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_beat >= HEARTBEAT_INTERVAL:
                try:
                    self._send_heartbeat()
                    last_beat = now
                except Exception as err:  # noqa: BLE001
                    raise LanError(f"心跳发送失败: {err}") from err

            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as err:
                raise LanError(f"读取失败: {err}") from err
            if not chunk:
                raise LanError("网关关闭了连接")
            self._last_rx = time.time()
            buf.extend(chunk)
            self._drain(buf)

    def _drain(self, buf: bytearray) -> None:
        """按 PACKLEN 头切帧。"""
        while True:
            idx = buf.find(FRAME_MAGIC)
            if idx < 0:
                # 没有帧头，保留尾部以防帧头被截断
                if len(buf) > 64:
                    del buf[:-8]
                return
            if idx > 0:
                del buf[:idx]
            if len(buf) < HEADER_LEN:
                return
            (length,) = struct.unpack(">I", bytes(buf[8:12]))
            if length <= 0 or length > 4 * 1024 * 1024:
                del buf[:HEADER_LEN]
                continue
            total = HEADER_LEN + length
            if len(buf) < total:
                return
            payload = bytes(buf[HEADER_LEN:total])
            del buf[:total]
            try:
                self._handle_frame(payload)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("安居家园处理局域网帧失败")

    def _handle_frame(self, payload: bytes) -> None:
        text = payload.decode("utf-8", "ignore")
        data = extract_json(text)
        if not data:
            return
        _LOGGER.debug("安居家园 <<< %s", text[:300])

        msg_id = data.get("msg_id")
        if isinstance(msg_id, int):
            with self._waiters_lock:
                waiter = self._waiters.pop(msg_id, None)
            if waiter is not None:
                event, holder = waiter
                holder["data"] = data
                event.set()

        subject = str(data.get("subject") or "")
        if subject in ("device/property_report", "ac_panel/property_report"):
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

    # ------------------------------------------------------------------ 发
    def _send_frame(self, payload: bytes) -> None:
        sock = self._sock
        if sock is None or not self._connected.is_set():
            raise LanError("局域网未连接")
        frame = _pack(payload)
        with self._send_lock:
            total = 0
            while total < len(frame):
                total += sock.send(frame[total:])

    def _route(self, route: str) -> bytes:
        return f"/lan/{self._gwtype}/{self._gwno}{route}\r\n".encode()

    def _send_raw(self, route: str, body: dict[str, Any]) -> None:
        payload = self._route(route) + json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        _LOGGER.debug("安居家园 >>> %s", payload[:300])
        self._send_frame(payload)

    def _send_heartbeat(self) -> None:
        body = {
            "msg_id": random.randint(1, 999_999),
            "subject": "heartbeat",
            "bind_sta": 1,
            "homeid": self._home_id,
        }
        self._send_raw(ROUTE_RECEIVE, body)

    # ------------------------------------------------------------------ 对外接口
    def request(
        self,
        subject: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        """发一条指令并等待同 msg_id 的回复（阻塞，需在线程池里调用）。"""
        if not self.connected:
            raise LanError("局域网未连接")
        if subject == "devicelistproperty/get":
            # 云端写法映射到局域网写法，方便通用服务跨通道使用
            subject = "devices/property_get"
        event = threading.Event()
        holder: dict[str, Any] = {}
        msg_id = random.randint(1, 999_999)

        # 先把 waiter 注册好，再发，避免回包比注册快
        with self._waiters_lock:
            self._waiters[msg_id] = (event, holder)
        try:
            self._send_raw(
                ROUTE_RECEIVE, self._envelope(subject, msg_id, body, self._home_id)
            )
            if not event.wait(timeout):
                raise LanError(f"{subject} 超时未响应")
        finally:
            with self._waiters_lock:
                self._waiters.pop(msg_id, None)
        if "error" in holder:
            raise LanError(holder["error"])
        return holder.get("data", {})

    def fire(self, subject: str, body: dict[str, Any] | None = None) -> None:
        if not self.connected:
            raise LanError("局域网未连接")
        if subject == "devicelistproperty/get":
            subject = "devices/property_get"
        self._send_raw(
            ROUTE_RECEIVE,
            self._envelope(
                subject, random.randint(1, 999_999), body, self._home_id
            ),
        )

    @staticmethod
    def _envelope(
        subject: str,
        msg_id: int,
        body: dict[str, Any] | None,
        home_id: str = "",
    ) -> dict[str, Any]:
        """指令外壳：subject + msg_id + homeid，业务字段平铺在同一层。"""
        inner: dict[str, Any] = {"subject": subject, "msg_id": msg_id}
        if body:
            inner.update(body)
        inner.setdefault("homeid", home_id)
        return inner

    def query_states(self, timeout: float = 12.0) -> dict[str, Any]:
        """读全屋状态（局域网 subject 是 devices/property_get）。"""
        return self.request("devices/property_get", None, timeout=timeout)

    def set_properties(
        self,
        mac: str,
        dev_type: int,
        index: int,
        properties: dict[int, Any],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """开关 / 调光。

        报文与云端完全一致：endpoints[].properties[{property_type,value}]。
        早期文档说局域网用 onoff / level 扁平字段，实测网关不认（不回复、状态不变），
        已改为 properties 写法。
        """
        return self.request(
            "device/property_set",
            self._set_body(mac, dev_type, index, properties),
            timeout=timeout,
        )

    @staticmethod
    def _set_body(
        mac: str, dev_type: int, index: int, properties: dict[int, Any]
    ) -> dict[str, Any]:
        """device/property_set 的局域网报文体（纯函数，便于离线校验）。"""
        return {
            "mac": mac,
            "dev_type": int(dev_type),
            "endpoints": [
                {
                    "index": int(index),
                    "properties": [
                        {"property_type": int(prop_type), "value": value}
                        for prop_type, value in sorted(properties.items())
                    ],
                }
            ],
        }

    # ------------------------------------------------------------------ 工具
    def _callback(self, func, *args) -> None:
        if func is None:
            return
        try:
            self._loop.call_soon_threadsafe(func, *args)
        except RuntimeError:  # 事件循环已关闭
            pass

    def _fail_waiters(self) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters.values())
            self._waiters.clear()
        for event, holder in waiters:
            holder["error"] = "局域网连接已断开"
            event.set()
