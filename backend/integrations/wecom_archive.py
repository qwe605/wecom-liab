"""企业微信会话内容存档 C SDK 适配器。

本模块负责动态库调用、RSA 随机密钥解密、消息正文解密和统一协议
转换。上层 Service 只能看到规范消息，不能接触 SDK 密文或企业微信原始字段。
"""

from __future__ import annotations

import base64
import ctypes
import json
import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from backend.config import Settings
MESSAGE_PROTOCOL_VERSION = "1.0"
CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


class _SdkSlice(ctypes.Structure):
    """映射企业微信 C SDK 返回字符串使用的 Slice 结构。"""

    _fields_ = [("buf", ctypes.c_char_p), ("len", ctypes.c_int)]


def _message_text(message: dict[str, Any]) -> str:
    """从不同消息类型中提取供页面展示和 AI 使用的简洁文本。

    Args:
        message: SDK 解密后的单条企业微信消息。

    Returns:
        可读文本；没有正文的媒体消息返回带类型的占位文本。
    """

    message_type = str(message.get("msgtype") or "text")
    block = message.get(message_type)
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        for key in ("content", "title", "displayname", "filename", "name"):
            value = block.get(key)
            if value not in (None, ""):
                return str(value)
    text = message.get("text")
    if isinstance(text, dict) and text.get("content") not in (None, ""):
        return str(text["content"])
    return f"[{message_type}]"


def _message_time(value: Any) -> str:
    """把企微毫秒时间戳转换为统一 ISO 时间文本。

    Args:
        value: 通常为 ``msgtime`` 毫秒时间戳，也允许秒级时间戳或 ISO 文本。

    Returns:
        带时区的 ISO 时间文本。
    """

    text = str(value or "").strip()
    if text.isdigit():
        timestamp = int(text)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(
            CHINA_TIMEZONE
        ).isoformat()
    return text or datetime.now(tz=timezone.utc).isoformat()


def _participants(message: dict[str, Any]) -> set[str]:
    """收集单条消息涉及的发送人与接收人 ID。

    Args:
        message: SDK 解密后的原始消息。

    Returns:
        去除空值后的参与者 ID 集合。
    """

    values: list[Any] = [message.get("from")]
    recipients = message.get("tolist") or []
    if isinstance(recipients, list):
        values.extend(recipients)
    elif recipients:
        values.append(recipients)
    return {str(value) for value in values if value not in (None, "")}


def normalize_message(
    message: dict[str, Any],
    sequence: int,
) -> dict[str, Any]:
    """把企业微信 SDK 明文转换为客服宝统一消息协议。

    Args:
        message: SDK 解密后的原始消息。
        sequence: GetChatData 返回的消息游标。
    Returns:
        可由 MessageRepository 幂等保存的规范消息。
    """

    sender_id = str(message.get("from") or "")
    is_external = sender_id.startswith(("wm", "wo"))
    sender_type = "customer" if is_external else "staff"
    content = _message_text(message)
    participant_ids = sorted(_participants(message))
    return {
        "protocol_version": MESSAGE_PROTOCOL_VERSION,
        "archive_seq": int(sequence),
        "seq": int(sequence),
        "msgtime": _message_time(message.get("msgtime")),
        "msgtype": str(message.get("msgtype") or "text"),
        "from": sender_id,
        "sender_name": sender_id,
        "sender_type": sender_type,
        "content": content,
        "text": {"content": content},
        "content_json": dict(message),
        "external_userid": sender_id if is_external else "",
        "staff_userid": sender_id if sender_type == "staff" else "",
        "chat_id": str(message.get("roomid") or ""),
        "participant_ids": participant_ids,
        "channel_type": "wecom",
    }


class NativeWeComArchiveSdk:
    """仅供独立子进程使用的企业微信原生 SDK 封装。

    该类会把 C 动态库加载进当前进程。生产业务进程禁止直接实例化本类，必须
    使用下方 ``WeComArchiveClient`` 通过子进程调用，避免原生 SDK 与
    SQLAlchemy 所加载的系统库发生内存冲突。
    """

    def __init__(self, settings: Settings):
        """加载 C SDK、初始化企业实例并读取当前版本 RSA 私钥。

        Args:
            settings: 包含企业 ID、存档 Secret、SDK 路径和私钥路径的配置。

        Raises:
            RuntimeError: 配置缺失、文件不存在或 SDK 初始化失败。
        """

        self.settings = settings
        self.seed_cursor = settings.wecom_archive_seed_seq
        self._lock = threading.Lock()
        self._closed = False
        self._validate_paths()
        self._sdk = ctypes.cdll.LoadLibrary(settings.wecom_archive_sdk_path)
        self._declare_signatures()
        self._instance = self._sdk.NewSdk()
        if not self._instance:
            raise RuntimeError("企业微信会话存档 SDK NewSdk 返回空指针")
        result = self._sdk.Init(
            self._instance,
            settings.wecom_corp_id.encode("utf-8"),
            settings.wecom_archive_secret.encode("utf-8"),
        )
        if result != 0:
            self._sdk.DestroySdk(self._instance)
            self._instance = None
            raise RuntimeError(f"企业微信会话存档 SDK 初始化失败，错误码: {result}")
        private_bytes = Path(settings.wecom_archive_private_key_path).read_bytes()
        self._private_key = serialization.load_pem_private_key(
            private_bytes,
            password=None,
        )

    def _validate_paths(self) -> None:
        """校验启用官方数据源所必需的配置和文件。

        Raises:
            RuntimeError: 任一凭据、版本或文件路径不可用。
        """

        required = {
            "WECOM_CORP_ID": self.settings.wecom_corp_id,
            "WECOM_ARCHIVE_SECRET": self.settings.wecom_archive_secret,
            "WECOM_ARCHIVE_SDK_PATH": self.settings.wecom_archive_sdk_path,
            "WECOM_ARCHIVE_PRIVATE_KEY_PATH": self.settings.wecom_archive_private_key_path,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"企业微信会话存档配置缺失: {', '.join(missing)}")
        if self.settings.wecom_archive_public_key_version <= 0:
            raise RuntimeError("WECOM_ARCHIVE_PUBLIC_KEY_VERSION 必须大于 0")
        for name in ("wecom_archive_sdk_path", "wecom_archive_private_key_path"):
            path = Path(getattr(self.settings, name))
            if not path.is_file():
                raise RuntimeError(f"企业微信会话存档文件不存在: {path}")

    def _declare_signatures(self) -> None:
        """声明 ctypes 函数签名，避免指针在 64 位系统中被错误截断。"""

        self._sdk.NewSdk.restype = ctypes.c_void_p
        self._sdk.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        self._sdk.Init.restype = ctypes.c_int
        self._sdk.GetChatData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_uint,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(_SdkSlice),
        ]
        self._sdk.GetChatData.restype = ctypes.c_int
        self._sdk.DecryptData.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(_SdkSlice),
        ]
        self._sdk.DecryptData.restype = ctypes.c_int
        self._sdk.DestroySdk.argtypes = [ctypes.c_void_p]
        self._sdk.NewSlice.restype = ctypes.POINTER(_SdkSlice)
        self._sdk.FreeSlice.argtypes = [ctypes.POINTER(_SdkSlice)]
        self._sdk.GetContentFromSlice.argtypes = [ctypes.POINTER(_SdkSlice)]
        # 必须保留 C 层原始地址，不能声明为 c_char_p。后者会让 ctypes 提前复制成
        # Python bytes，再按 SDK 长度读取时可能越过复制缓冲区并在 FreeSlice 时崩溃。
        self._sdk.GetContentFromSlice.restype = ctypes.c_void_p
        self._sdk.GetSliceLen.argtypes = [ctypes.POINTER(_SdkSlice)]
        self._sdk.GetSliceLen.restype = ctypes.c_int

    def _slice_text(self, slice_pointer: ctypes.POINTER(_SdkSlice)) -> str:
        """读取 SDK Slice 的 UTF-8 内容。

        Args:
            slice_pointer: SDK 创建并由调用方负责释放的 Slice 指针。

        Returns:
            Slice 内的 UTF-8 文本，空内容返回空字符串。
        """

        content = self._sdk.GetContentFromSlice(slice_pointer)
        length = self._sdk.GetSliceLen(slice_pointer)
        if not content or length <= 0:
            return ""
        return ctypes.string_at(content, length).decode("utf-8")

    def get_encrypted_page(self, seq: int, limit: int) -> list[dict[str, Any]]:
        """调用 GetChatData 拉取一页加密会话记录。

        Args:
            seq: 上次成功返回的最大 seq，接口从 ``seq + 1`` 开始返回。
            limit: 本页条数，最终会限制在官方允许的 1 到 1000。

        Returns:
            含 seq、msgid、公钥版本和两段密文的记录列表。

        Raises:
            RuntimeError: SDK 调用失败或企业微信返回业务错误。
        """

        safe_limit = min(max(int(limit), 1), self.settings.wecom_archive_page_size, 1000)
        slice_pointer = self._sdk.NewSlice()
        if not slice_pointer:
            raise RuntimeError("企业微信会话存档 SDK NewSlice 返回空指针")
        try:
            with self._lock:
                code = self._sdk.GetChatData(
                    self._instance,
                    ctypes.c_ulonglong(max(0, int(seq))),
                    ctypes.c_uint(safe_limit),
                    b"",
                    b"",
                    ctypes.c_int(self.settings.wecom_archive_timeout_seconds),
                    slice_pointer,
                )
                text = self._slice_text(slice_pointer)
            if code != 0:
                raise RuntimeError(f"企业微信 GetChatData 调用失败，错误码: {code}")
            payload = json.loads(text or "{}")
            if int(payload.get("errcode") or 0) != 0:
                raise RuntimeError(
                    "企业微信 GetChatData 业务错误: "
                    f"{payload.get('errcode')} {payload.get('errmsg')}"
                )
            return list(payload.get("chatdata") or [])
        finally:
            self._sdk.FreeSlice(slice_pointer)

    def decrypt_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """使用对应版本私钥和 C SDK 解密一条会话记录。

        Args:
            record: GetChatData 返回的一条加密记录。

        Returns:
            合并 ``seq`` 与 ``msgid`` 后的消息明文。

        Raises:
            RuntimeError: 公钥版本不匹配、RSA 解密失败或 SDK 解密失败。
        """

        version = int(record.get("publickey_ver") or 0)
        expected = self.settings.wecom_archive_public_key_version
        if version != expected:
            raise RuntimeError(
                f"消息公钥版本为 {version}，当前仅配置版本 {expected} 的私钥"
            )
        encrypted_key = base64.b64decode(
            str(record.get("encrypt_random_key") or ""),
            validate=True,
        )
        plain_key = self._private_key.decrypt(encrypted_key, padding.PKCS1v15())
        encrypted_message = str(record.get("encrypt_chat_msg") or "")
        slice_pointer = self._sdk.NewSlice()
        if not slice_pointer:
            raise RuntimeError("企业微信会话存档 SDK NewSlice 返回空指针")
        try:
            with self._lock:
                code = self._sdk.DecryptData(
                    plain_key,
                    encrypted_message.encode("utf-8"),
                    slice_pointer,
                )
                text = self._slice_text(slice_pointer)
            if code != 0:
                raise RuntimeError(f"企业微信 DecryptData 调用失败，错误码: {code}")
            # 企业微信历史消息中可能包含未转义的换行、制表或其他控制字符。
            # Python 默认严格模式会拒绝整条消息并阻断游标推进；strict=False 仅放宽
            # JSON 字符串控制字符解析，不改变字段结构或执行其中内容。
            message = json.loads(text, strict=False)
            message.setdefault("msgid", record.get("msgid"))
            message["seq"] = int(record.get("seq") or 0)
            return message
        finally:
            self._sdk.FreeSlice(slice_pointer)

    def close(self) -> None:
        """释放 C SDK 企业实例；重复调用不会产生副作用。"""

        if not self._closed and getattr(self, "_instance", None):
            self._sdk.DestroySdk(self._instance)
            self._instance = None
            self._closed = True

    def __del__(self) -> None:
        """在请求对象被回收时兜底释放 C SDK 资源。"""

        try:
            self.close()
        except Exception:
            # 析构阶段不能让释放异常覆盖业务响应。
            pass


class WeComArchiveClient:
    """通过独立子进程访问企业微信会话内容存档 SDK。

    本类可以安全地在 FastAPI、SQLAlchemy 和后台同步进程中使用。每次 SDK
    调用都在短生命周期子进程内完成，父进程只接收 JSON，不加载原生动态库。
    """

    def __init__(self, settings: Settings):
        """保存官方存档配置，不在当前业务进程加载 C SDK。

        Args:
            settings: 企业微信会话内容存档及子进程超时配置。
        """

        self.settings = settings
        self.seed_cursor = settings.wecom_archive_seed_seq
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._request_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None

    def _start_worker(self) -> subprocess.Popen[str]:
        """启动或复用仅加载企业微信原生 SDK 的长驻子进程。

        Returns:
            标准输入、输出均使用逐行 JSON 协议的可用子进程。

        Raises:
            RuntimeError: 子进程启动后立即退出或管道创建失败。
        """

        if self._process is not None and self._process.poll() is None:
            return self._process
        self.close()
        project_root = Path(__file__).resolve().parents[2]
        process = subprocess.Popen(
            [sys.executable, "-m", "backend.integrations.wecom_archive_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=project_root,
        )
        if process.stdin is None or process.stdout is None:
            process.terminate()
            raise RuntimeError("企业微信会话存档 SDK 子进程管道创建失败")
        self._process = process
        responses: queue.Queue[str | None] = queue.Queue()
        self._responses = responses
        self._reader_thread = threading.Thread(
            target=self._read_worker_output,
            args=(process, responses),
            name="wecom-archive-reader",
            daemon=True,
        )
        self._reader_thread.start()
        return process

    def _read_worker_output(
        self,
        process: subprocess.Popen[str],
        responses: queue.Queue[str | None],
    ) -> None:
        """持续读取子进程响应并放入线程安全队列。

        Args:
            process: 当前正在运行的 SDK 子进程。
            responses: 只属于当前进程的响应队列，避免重启时串入旧结束信号。
        """

        assert process.stdout is not None
        for line in process.stdout:
            responses.put(line)
        responses.put(None)

    def _run_worker(
        self,
        seq: int,
        limit: int,
        decrypt: bool,
    ) -> list[dict[str, Any]]:
        """调用 SDK 子进程拉取一页消息。

        Args:
            seq: 上次成功处理的企业微信全局游标。
            limit: 本页最多拉取条数。
            decrypt: 是否在子进程中完成 RSA 和消息正文解密。

        Returns:
            未解密模式返回游标元数据；解密模式返回企业微信消息明文。

        Raises:
            RuntimeError: 子进程超时、异常退出或返回格式无效。
        """

        request = {
            "action": "page",
            "seq": max(0, int(seq)),
            "limit": min(max(int(limit), 1), 1000),
            "decrypt": bool(decrypt),
        }
        with self._request_lock:
            process = self._start_worker()
            assert process.stdin is not None
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                response = self._responses.get(
                    timeout=self.settings.wecom_archive_timeout_seconds + 20
                )
            except (BrokenPipeError, OSError) as exc:
                self.close()
                raise RuntimeError("企业微信会话存档 SDK 子进程连接已断开") from exc
            except queue.Empty as exc:
                self.close()
                raise RuntimeError("企业微信会话存档 SDK 子进程调用超时") from exc
            if response is None:
                detail = ""
                if process.stderr is not None:
                    detail = process.stderr.read().strip()
                self.close()
                raise RuntimeError(
                    f"企业微信会话存档 SDK 子进程失败: {detail or process.poll()}"
                )
        try:
            payload = json.loads(response or "{}")
        except json.JSONDecodeError as exc:
            self.close()
            raise RuntimeError("企业微信会话存档 SDK 子进程返回了无效 JSON") from exc
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "企业微信会话存档调用失败"))
        return list(payload.get("records") or [])

    def close(self) -> None:
        """结束长驻 SDK 子进程并释放所有管道资源。"""

        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write('{"action":"close"}\n')
                process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=3)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
        if process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def __del__(self) -> None:
        """对象回收时兜底关闭 SDK 子进程。"""

        try:
            self.close()
        except Exception:
            pass

    def fetch_page(self, start_seq: int, limit: int) -> list[dict[str, Any]]:
        """通过独立 SDK 子进程拉取并规范化一页全局消息。

        Args:
            start_seq: 已完整提交的全局归档序号。
            limit: 本页最大消息数。

        Returns:
            按归档序号升序排列的全部可解密消息，不按已登记会话筛选。
        """

        rows = self._run_worker(start_seq, limit, decrypt=True)
        normalized: list[dict[str, Any]] = []
        for message in rows:
            sequence = int(message.get("seq") or 0)
            if sequence <= int(start_seq):
                continue
            normalized.append(normalize_message(message, sequence=sequence))
        normalized.sort(key=lambda item: int(item["archive_seq"]))
        return normalized
