"""企业微信会话内容存档原生 SDK 的独立进程入口。

本模块只读取标准输入中的 JSON 请求，调用原生 SDK，并把结果以 JSON 写到标准
输出。它不导入 SQLAlchemy、不访问 MySQL，也不承载游标和会话业务规则。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from backend.config import get_settings
from backend.integrations.wecom_archive import NativeWeComArchiveSdk


def execute(
    request: dict[str, Any],
    sdk: NativeWeComArchiveSdk,
) -> dict[str, Any]:
    """执行一次原生 SDK 拉取请求。

    Args:
        request: 包含 action、seq、limit 和 decrypt 的进程请求。
        sdk: 当前子进程启动时创建并持续复用的原生 SDK 实例。

    Returns:
        可序列化的成功结果，records 为游标元数据或消息明文。

    Raises:
        ValueError: action 非法或请求参数不可用。
        RuntimeError: SDK 初始化、拉取或解密失败。
    """

    if request.get("action") != "page":
        raise ValueError("企业微信 SDK 子进程仅支持 page 操作")
    seq = max(0, int(request.get("seq") or 0))
    limit = min(max(int(request.get("limit") or 1000), 1), 1000)
    decrypt = bool(request.get("decrypt"))
    encrypted = sdk.get_encrypted_page(seq, limit)
    if not decrypt:
        records = [
            {
                "seq": int(item.get("seq") or 0),
                "msgid": str(item.get("msgid") or ""),
            }
            for item in encrypted
        ]
    else:
        records = [sdk.decrypt_record(item) for item in encrypted]
    return {"ok": True, "records": records}


def main() -> int:
    """初始化一次 SDK，并持续处理标准输入中的逐行 JSON 请求。

    Returns:
        正常收到 close 或输入结束时返回 0；SDK 初始化失败返回 1。单次请求失败
        会返回结构化错误但不退出进程，允许后台下一轮继续重试。
    """

    try:
        sdk = NativeWeComArchiveSdk(get_settings())
    except Exception as exc:
        sys.stderr.write(str(exc))
        return 1
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line or "{}")
                if request.get("action") == "close":
                    break
                result = execute(request, sdk)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return 0
    finally:
        sdk.close()


if __name__ == "__main__":
    raise SystemExit(main())
