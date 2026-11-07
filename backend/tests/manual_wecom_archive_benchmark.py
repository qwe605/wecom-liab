"""企业微信会话存档 SDK 的手工性能与限流测试。

本脚本只输出数量、延迟和错误码，不输出聊天正文、密文或任何凭据。它不属于
自动化单元测试，需在已配置可信 IP、SDK、Secret 和私钥的服务器上主动运行。

示例：
    python -m backend.tests.manual_wecom_archive_benchmark \
        --page-rounds 10 --poll-workers 12 --poll-seconds 10
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from backend.config import get_settings
from backend.integrations.wecom_archive import NativeWeComArchiveSdk


@dataclass
class LatencySummary:
    """一组接口耗时的毫秒统计结果。"""

    count: int
    minimum_ms: float
    median_ms: float
    p95_ms: float
    maximum_ms: float
    average_ms: float


def summarize(values: list[float]) -> LatencySummary:
    """计算延迟样本的最小值、中位数、P95、最大值和平均值。

    Args:
        values: 毫秒耗时列表，不能为空。

    Returns:
        稳定、可 JSON 序列化的延迟统计。

    Raises:
        ValueError: 未提供任何样本。
    """

    if not values:
        raise ValueError("延迟样本不能为空")
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return LatencySummary(
        count=len(ordered),
        minimum_ms=round(ordered[0], 2),
        median_ms=round(statistics.median(ordered), 2),
        p95_ms=round(ordered[p95_index], 2),
        maximum_ms=round(ordered[-1], 2),
        average_ms=round(statistics.fmean(ordered), 2),
    )


def find_tail(client: NativeWeComArchiveSdk, start_seq: int) -> dict[str, Any]:
    """从指定游标探测当前消息流末尾。

    Args:
        client: 已初始化的官方 SDK 适配器。
        start_seq: 已知的安全起始游标。

    Returns:
        不含消息正文的末尾探测结果。
    """

    started = time.perf_counter()
    cursor = max(0, int(start_seq))
    pages = 0
    while pages < 10000:
        rows = client.get_encrypted_page(cursor, 1000)
        pages += 1
        if not rows:
            break
        next_cursor = max(int(item.get("seq") or 0) for item in rows)
        if next_cursor <= cursor:
            raise RuntimeError("企业微信归档页未推进游标")
        cursor = next_cursor
    else:
        raise RuntimeError("末尾探测超过 10000 页，请提高起始游标后重试")
    return {
        "cursor": cursor,
        "reached_end": True,
        "pages": pages,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def benchmark_pages(
    client: NativeWeComArchiveSdk,
    sample_seq: int,
    limit: int,
    rounds: int,
) -> dict[str, Any]:
    """重复拉取同一页，测量 GetChatData 的稳定延迟和吞吐。

    Args:
        client: 已初始化的官方 SDK 适配器。
        sample_seq: 样本页起始游标。
        limit: 每页条数。
        rounds: 重复次数。

    Returns:
        页数、消息数、延迟和每秒消息数统计。
    """

    latencies: list[float] = []
    total_rows = 0
    for _ in range(max(1, rounds)):
        started = time.perf_counter()
        rows = client.get_encrypted_page(sample_seq, limit)
        latencies.append((time.perf_counter() - started) * 1000)
        total_rows += len(rows)
    elapsed_seconds = sum(latencies) / 1000
    return {
        "sample_seq": sample_seq,
        "limit": limit,
        "rounds": max(1, rounds),
        "rows": total_rows,
        "messages_per_second": round(total_rows / elapsed_seconds, 2)
        if elapsed_seconds
        else 0,
        "latency": asdict(summarize(latencies)),
    }


def benchmark_decryption(
    client: NativeWeComArchiveSdk,
    sample_seq: int,
    limit: int,
) -> dict[str, Any]:
    """测量 RSA 与 SDK 正文解密耗时，不输出解密内容。

    Args:
        client: 已初始化的官方 SDK 适配器。
        sample_seq: 样本页起始游标。
        limit: 最多解密条数。

    Returns:
        成功数、失败数和单条解密延迟统计。
    """

    rows = client.get_encrypted_page(sample_seq, min(max(limit, 1), 1000))
    latencies: list[float] = []
    errors = 0
    for row in rows:
        started = time.perf_counter()
        try:
            client.decrypt_record(row)
            latencies.append((time.perf_counter() - started) * 1000)
        except Exception:
            errors += 1
    return {
        "attempted": len(rows),
        "succeeded": len(latencies),
        "failed": errors,
        "latency": asdict(summarize(latencies)) if latencies else None,
    }


def benchmark_empty_polling(
    tail_seq: int,
    workers: int,
    duration_seconds: int,
) -> dict[str, Any]:
    """在当前流末尾并发空轮询，检查是否出现提前限流或 SDK 错误。

    测试强度由调用方控制。默认参数低于官方 4000 次/分钟上限，不主动冲击生产
    限额，只验证在预期业务并发下是否出现异常。

    Args:
        tail_seq: 已确认的当前消息流末尾。
        workers: 并发 SDK 实例数。
        duration_seconds: 每个工作线程持续轮询秒数。

    Returns:
        请求量、实际请求速率、空页数、错误分布和延迟统计。
    """

    deadline = time.monotonic() + max(1, duration_seconds)
    lock = threading.Lock()
    latencies: list[float] = []
    empty_pages = 0
    errors: dict[str, int] = {}

    def worker() -> None:
        """为单个线程创建独立 SDK 实例并持续拉取空页。"""

        nonlocal empty_pages
        client = NativeWeComArchiveSdk(get_settings())
        try:
            while time.monotonic() < deadline:
                started = time.perf_counter()
                try:
                    rows = client.get_encrypted_page(tail_seq, 1)
                    elapsed = (time.perf_counter() - started) * 1000
                    with lock:
                        latencies.append(elapsed)
                        empty_pages += int(not rows)
                except Exception as exc:
                    key = str(exc)
                    with lock:
                        errors[key] = errors.get(key, 0) + 1
        finally:
            client.close()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, workers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    attempts = len(latencies) + sum(errors.values())
    return {
        "workers": max(1, workers),
        "duration_seconds": round(elapsed, 2),
        "attempts": attempts,
        "successful_calls": len(latencies),
        "empty_pages": empty_pages,
        "errors": errors,
        "requests_per_second": round(attempts / elapsed, 2) if elapsed else 0,
        "projected_requests_per_minute": round(attempts / elapsed * 60, 2)
        if elapsed
        else 0,
        "latency": asdict(summarize(latencies)) if latencies else None,
    }


def main() -> None:
    """解析参数并顺序执行末尾探测、拉取、解密和空轮询测试。"""

    parser = argparse.ArgumentParser(description="企业微信会话存档 SDK 性能测试")
    parser.add_argument("--start-seq", type=int, default=None)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--page-rounds", type=int, default=10)
    parser.add_argument("--decrypt-limit", type=int, default=100)
    parser.add_argument("--poll-workers", type=int, default=12)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    settings = get_settings()
    client = NativeWeComArchiveSdk(settings)
    try:
        start_seq = settings.wecom_archive_seed_seq if args.start_seq is None else args.start_seq
        tail = find_tail(client, start_seq)
        sample_seq = max(0, int(tail["cursor"]) - min(max(args.page_limit, 1), 1000))
        result = {
            "official_limit_requests_per_minute": 4000,
            "tail_discovery": tail,
            "page_pull": benchmark_pages(
                client,
                sample_seq=sample_seq,
                limit=args.page_limit,
                rounds=args.page_rounds,
            ),
            "decryption": benchmark_decryption(
                client,
                sample_seq=sample_seq,
                limit=args.decrypt_limit,
            ),
        }
    finally:
        client.close()
    result["empty_polling"] = benchmark_empty_polling(
        tail_seq=int(tail["cursor"]),
        workers=args.poll_workers,
        duration_seconds=args.poll_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
