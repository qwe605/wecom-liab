"""第三方接口使用的轻量 JSON HTTP 客户端。"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("kefubao.integrations.http")


def _decode_json_object(raw: bytes, charset: str | None = None) -> dict[str, Any]:
    """把第三方响应字节解析为 JSON 对象。

    企业微信资料中可能包含历史昵称或群名产生的非标准 UTF-8 字节。若严格
    解码失败，使用替换字符保留其余合法字段，避免一个异常名称中断整次员工、
    客户和群资料同步。这里只容错字符编码，JSON 结构错误仍会正常抛出。

    Args:
        raw: HTTP 响应原始字节。
        charset: 响应头声明的字符集；为空时按企业微信默认 UTF-8 处理。

    Returns:
        解析后的 JSON 对象。

    Raises:
        UnicodeDecodeError: 声明字符集不可用且 UTF-8 也无法严格解码时不会抛出，
            而是进入替换字符容错。
        json.JSONDecodeError: 响应不是合法 JSON。
        RuntimeError: JSON 顶层不是对象。
    """

    encoding = (charset or "utf-8").strip() or "utf-8"
    try:
        text = raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")
        LOGGER.warning("第三方 JSON 响应包含非法字符，已替换异常字节后继续解析")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError("第三方接口没有返回 JSON 对象")
    return payload


def get_json(url: str, timeout: int = 20) -> dict[str, Any]:
    """发送 GET 请求并解析 JSON。

    Raises:
        RuntimeError: 响应不是 JSON 对象或网络请求失败。
    """

    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return _decode_json_object(
            response.read(),
            response.headers.get_content_charset(),
        )


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """发送 JSON POST 请求并解析响应。"""

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=timeout) as response:
        return _decode_json_object(
            response.read(),
            response.headers.get_content_charset(),
        )
