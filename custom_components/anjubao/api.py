"""安居家园 Plus 厂商云接口封装。

认证链路（App 1.7.2 反编译 + 实测确认）：
  账号密码登录 -> accessToken / refreshToken
  accessToken 过期用 refreshToken 免密续期（不轮换）
  控制走 MQTT，连接前必须先用 accessToken 换一次性票据（appauth）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import aiohttp

from .const import (
    APP_KEY,
    APP_SECRET,
    APP_VERSION,
    APPAUTH_PATH,
    BASE_CLOUD,
    BASE_ENTRANCE,
    DEVICE_LIST_PATH,
    GATEWAY_LIST_PATH,
    HOME_LIST_PATH,
    LOGIN_PATH,
    REFRESH_PATH,
    USER_AGENT,
    USER_INFO_PATH,
)

_LOGGER = logging.getLogger(__name__)


class AnjubaoError(Exception):
    """基础异常。"""


class AnjubaoAuthError(AnjubaoError):
    """账号密码错误 / 令牌失效。"""


class AnjubaoApiError(AnjubaoError):
    """接口返回业务错误。"""


def _sign(params: dict[str, Any], token: str, timestamp: str) -> str:
    """厂商签名：Base64(MD5hex(HMAC_SHA1(sorted "k=v&", appSecret)))。

    空值字段会被 App 过滤掉，这里保持一致。
    """
    payload = {
        "appKey": APP_KEY,
        "Authorization": token or "",
        "timeStamp": timestamp,
        "version": "v1",
    }
    for key, value in params.items():
        if value is None:
            continue
        payload[str(key)] = str(value)
    pairs = [f"{k}={payload[k]}" for k in sorted(payload) if payload[k] != ""]
    raw = "&".join(pairs) + APP_SECRET
    digest = hmac.new(APP_SECRET.encode(), raw.encode(), hashlib.sha1).digest()
    return base64.b64encode(hashlib.md5(digest).hexdigest().encode()).decode()


class AnjubaoCloudApi:
    """HTTP 侧：登录、刷新令牌、拉取家庭/网关/设备。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._session = session
        self.username = username
        self.password = password
        self.access_token = access_token or ""
        self.refresh_token = refresh_token or ""
        self.user_id = user_id or ""
        # 登录接口返回的账号 id，与 MQTT 使用的 userId 不是同一个
        self.ajjy_id = ""
        self._refresh_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 基础请求
    def _headers(self, token: str, params: dict[str, Any]) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "appKey": APP_KEY,
            "appSecret": APP_SECRET,
            "appVersionCode": APP_VERSION,
            "timeStamp": ts,
            "Authorization": token,
            "sign": _sign(params, token, ts),
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json;charset=utf-8",
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        query = {k: v for k, v in (params or {}).items() if v is not None}
        # 签名只覆盖 query 参数 + 固定字段；body 不参与
        headers = self._headers(self.access_token if auth else "", query)
        try:
            async with self._session.request(
                method,
                url,
                params=query or None,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise AnjubaoError(f"网络异常: {err}") from err

        try:
            data = await asyncio.to_thread(_loads, text)
        except ValueError as err:
            raise AnjubaoError(f"响应解析失败: {text[:120]}") from err
        return data

    @staticmethod
    def _code(data: dict[str, Any]) -> int:
        code = data.get("code")
        try:
            return int(code)
        except (TypeError, ValueError):
            return 200 if code in ("success", "0") else -1

    # ------------------------------------------------------------------ 登录
    async def async_login(self) -> dict[str, Any]:
        """账号密码登录。

        App 的请求体是 {"phone": Base64(手机号), "password": MD5(密码)}，
        两者都是必填的预处理，少一步服务端就返回"账号或者密码错误"。
        """
        if not self.username or not self.password:
            raise AnjubaoAuthError("缺少账号或密码")
        phone = base64.b64encode(self.username.strip().encode()).decode()
        password = hashlib.md5(self.password.strip().encode()).hexdigest()
        data = await self._request(
            "POST",
            BASE_ENTRANCE + LOGIN_PATH,
            body={"phone": phone, "password": password},
            auth=False,
        )
        code = self._code(data)
        if code != 200:
            raise AnjubaoAuthError(f"登录失败: {data.get('message') or data.get('msg') or data}")
        payload = data.get("data") or data.get("datas") or {}
        token = payload.get("accessToken") or payload.get("token")
        if not token:
            raise AnjubaoAuthError(f"登录响应缺少令牌: {str(payload)[:120]}")
        self.access_token = token
        self.refresh_token = payload.get("refreshToken") or self.refresh_token
        # 登录返回的 id 是账号 id（ajjyid），不是 MQTT 用的 userId；
        # 真正的 userId 在 getUserInfo 的 datas.id 里，由 async_get_user_info 覆盖。
        self.ajjy_id = payload.get("id") or payload.get("userId") or self.ajjy_id
        return payload

    async def async_refresh(self) -> None:
        """refreshToken 换 accessToken，无需签名。"""
        async with self._refresh_lock:
            if not self.refresh_token:
                raise AnjubaoAuthError("refreshToken 缺失，请重新登录")
            phone = base64.b64encode((self.username or "").encode()).decode()
            try:
                async with self._session.post(
                    BASE_ENTRANCE + REFRESH_PATH,
                    json={"refreshToken": self.refresh_token, "phone": phone},
                    headers={
                        "Content-Type": "application/json;charset=utf-8",
                        "User-Agent": USER_AGENT,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise AnjubaoError(f"刷新令牌网络异常: {err}") from err
            if self._code(data) != 200:
                raise AnjubaoAuthError(f"刷新令牌失败: {data}")
            payload = data.get("data") or {}
            self.access_token = payload.get("accessToken") or self.access_token
            self.refresh_token = payload.get("refreshToken") or self.refresh_token

    async def _renew(self) -> None:
        """令牌续期：先用 refreshToken，失败再回落到账号密码登录。"""
        try:
            await self.async_refresh()
        except AnjubaoError:
            if not (self.username and self.password):
                raise
            await self.async_login()

    async def _authed(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        data = await self._request(method, url, auth=True, **kwargs)
        # 401 = token 过期；700 = token 缺失/校验失败
        if self._code(data) in (401, 700):
            await self._renew()
            data = await self._request(method, url, auth=True, **kwargs)
        code = self._code(data)
        if code != 200:
            raise AnjubaoApiError(f"{url} 返回 {data.get('message') or data.get('msg') or data}")
        return data

    # ------------------------------------------------------------------ 业务数据
    async def async_get_user_info(self) -> dict[str, Any]:
        data = await self._authed("GET", BASE_CLOUD + USER_INFO_PATH)
        info = data.get("datas") or {}
        if not self.user_id:
            self.user_id = info.get("id", "")
        return info

    async def async_get_homes(self) -> list[dict[str, Any]]:
        data = await self._authed(
            "GET", BASE_CLOUD + HOME_LIST_PATH, params={"masterid": self.user_id}
        )
        datas = data.get("datas") or {}
        return datas.get("resultList") or []

    async def async_get_gateways(self, home_id: str) -> list[dict[str, Any]]:
        data = await self._authed(
            "GET", BASE_CLOUD + GATEWAY_LIST_PATH, params={"homeid": home_id}
        )
        return data.get("datas") or []

    async def async_get_devices(self, gateway_id: str) -> list[dict[str, Any]]:
        data = await self._authed(
            "GET",
            BASE_CLOUD + DEVICE_LIST_PATH,
            params={"gwid": gateway_id, "pageNo": 1, "pageSize": 100},
        )
        datas = data.get("datas") or {}
        return datas.get("resultList") or []

    async def async_appauth(self) -> dict[str, Any]:
        """换取 MQTT 一次性票据。body 必须为空，带 phone 会被判非法用户。"""
        params = {"userid": self.user_id}
        data = await self._request(
            "POST",
            BASE_CLOUD + APPAUTH_PATH,
            params=params,
            body=None,
            auth=True,
        )
        code = self._code(data)
        if code in (401, 700):
            await self._renew()
            data = await self._request(
                "POST", BASE_CLOUD + APPAUTH_PATH, params=params, body=None, auth=True
            )
            code = self._code(data)
        if code != 0 and code != 200:
            raise AnjubaoApiError(f"appauth 失败: {data.get('message') or data}")
        address = data.get("address") or {}
        if not address.get("secretkey"):
            raise AnjubaoApiError(f"appauth 未返回票据: {str(data)[:160]}")
        return address


def _loads(text: str) -> dict[str, Any]:
    import json

    return json.loads(text)
