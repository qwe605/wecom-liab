"""MiniMax 等大语言模型的第三方接口适配。

当前使用 OpenAI 兼容接口（base_url + api_key），支持任何提供 OpenAI 兼容 API 的模型服务。
已在以下模型上验证：
- DeepSeek（deepseek-chat）
- 硅基流动（siliconflow）
- 其他 OpenAI 兼容端点

核心职责：
1. 构造符合 OpenAI Chat Completions 格式的请求体
2. 处理超时、HTTP 错误、响应结构异常
3. 解析模型原始输出（可能是普通文本、Markdown 代码块、JSON 等），提取出结构化 JSON
4. 将结构化 JSON 映射为 GeneratedReply 对象
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from json_repair import repair_json

from backend.schemas.ai import GeneratedReply


def _completion_url(base_url: str) -> str:
    """把 base_url 规范化为 chat/completions 端点路径。

    支持两种格式：
    - https://api.deepseek.com/v1          → https://api.deepseek.com/v1/chat/completions
    - https://api.deepseek.com/v1/chat/completions   → 直接使用
    """
    base = base_url.rstrip("/")
    return f"{base}/chat/completions"


def _model_content_to_text(content: Any) -> str:
    """把 OpenAI 兼容接口的多种 ``content`` 形态统一为文本。

    正常情况下模型返回字符串，但部分推理模型或兼容网关会返回文本块数组，
    例如 ``[{"type": "text", "text": "{...}"}]``。直接对数组调用 ``str``
    会得到 Python 字典表示（单引号），随后即使使用 ``json_repair`` 也可能
    解析失败。这里仅提取文本块，不把 ``reasoning_content`` 当作最终答案，
    避免把模型思考过程误当成客服可见内容。

    Args:
        content: ``choices[0].message.content`` 的原始值。

    Returns:
        可交给 JSON 提取器处理的文本；空值返回空字符串。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content"):
            value = content.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                block_type = str(item.get("type") or "").casefold()
                if block_type in {"thinking", "reasoning", "reasoning_content"}:
                    continue
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return str(content)


def _balanced_object_candidates(text: str) -> list[str]:
    """提取文本中结构完整的 JSON 对象片段。

    不能用 ``find('{')`` 加 ``rfind('}')``，因为模型前后说明文字或 JSON
    字符串值本身包含花括号时，会把多个结构拼成一个非法候选。扫描时跳过
    字符串内部的花括号，并支持转义引号；该函数只负责切片，合法性仍由
    ``json.loads`` 或 ``json_repair`` 校验。

    Args:
        text: 已去除外层包装的模型输出。

    Returns:
        按文本出现顺序排列的对象片段。
    """
    candidates: list[str] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
    return candidates


def _extract_json_object(text: Any) -> dict:
    """从模型原始输出中提取 JSON 对象。

    模型输出格式不确定，可能为：
    1. 纯净 JSON：{"summary": "...", ...}
    2. Markdown 代码块：```json\n{"summary": "...", ...}\n```
    3. 普通文本中间夹着 JSON：随便说了几句...{"summary": "..."}...后面又说了些

    本函数按优先级尝试多种解析策略：
    - 尝试直接解析（处理格式 1）
    - 去掉 markdown 代码块标记后解析（处理格式 2）
    - 提取文本中结构完整的 { ... } 区间（处理格式 3 的情况）
    - 用 json_repair.repair_json 做容错解析
    - 都失败则抛出 ValueError
    """
    raw_text = _model_content_to_text(text)
    # 只移除模型包装，不修改 JSON 字符串里的反斜杠。旧实现把 ``\\\\``
    # 强行压成 ``\\``，会破坏 Windows 路径、转义引号等合法 JSON；“换一个”
    # 返回不同内容时更容易触发这个隐蔽问题。
    normalized = raw_text.replace("\ufeff", "").strip()
    normalized = re.sub(r"<think>.*?</think>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"^\s*```(?:json|JSON)?\s*", "", normalized)
    normalized = re.sub(r"\s*```\s*$", "", normalized).strip()
    candidates = [normalized]
    candidates.extend(_balanced_object_candidates(normalized))

    # 标准 JSON 解析可能识别出嵌套对象；把它加入候选前面，优先使用完整对象。
    decoder = json.JSONDecoder()
    decoded_candidates: list[str] = []
    for start, char in enumerate(normalized):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(normalized, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            decoded_candidates.append(normalized[start:end])
    candidates[1:1] = decoded_candidates

    payload = None
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    # 尝试用 json_repair 做容错解析（能处理 trailing comma、注释等非标准 JSON）
    if not isinstance(payload, dict):
        for candidate in candidates:
            try:
                payload = repair_json(
                    candidate,
                    return_objects=True,
                    skip_json_loads=True,
                )
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                break

    if not isinstance(payload, dict):
        raise ValueError("模型返回内容无法解析为 JSON 对象，请重试")
    return payload


@dataclass(frozen=True)
class OpenAICompatibleChatClient:
    """OpenAI 兼容接口的 LLM 客户端。

    配置项通过环境变量注入：
    - AI_MODEL_BASE_URL  ：模型服务地址（如 https://api.deepseek.com/v1）
    - AI_MODEL_API_KEY   ：API 密钥
    - AI_MODEL_NAME      ：模型名称（如 deepseek-chat）
    - AI_MODEL_TIMEOUT_SECONDS：请求超时时间，默认 30 秒

    设计决策：使用原生 urllib 而非 requests/httpx，保持项目轻量无额外依赖。
    """

    base_url: str                        # 模型服务的 base URL
    api_key: str                         # API 密钥
    model_name: str                      # 模型名称，会出现在请求体的 model 字段
    timeout_seconds: int = 30            # 请求超时（秒）

    def generate(self, system_prompt: str, user_prompt: str) -> GeneratedReply:
        """调用 LLM 生成回复建议。

        请求策略：
        - temperature=0.2：轻微随机性，保证输出稳定但不过于机械
        - response_format={"type": "json_object"}：强制模型输出 JSON（部分模型支持）
        - messages 只有 system + user 两轮（无历史消息，context 全靠 user_prompt 承载）

        @param system_prompt  ：系统提示词（包含角色定义、输出格式要求、约束规则）
        @param user_prompt    ：用户提示词（包含会话上下文、知识证据、AI 任务描述）
        @return              ：GeneratedReply 结构化对象
        """
        result, latency_ms = self._complete_json(system_prompt, user_prompt)
        return GeneratedReply(
            summary=str(result.get("summary") or "").strip(),
            customer_intent=str(result.get("customer_intent") or "").strip(),
            suggested_reply=str(result.get("suggested_reply") or "").strip(),
            # risk_level 默认 low，模型必须返回 low/medium/high 之一
            risk_level=str(result.get("risk_level") or "low").strip().lower(),
            # used_knowledge_ids：模型认为它参考了哪些知识（由 validate 阶段校验是否合法）
            used_knowledge_ids=[
                str(item)
                for item in result.get("used_knowledge_ids", [])
                if str(item).strip()
            ],
            model_name=self.model_name,
            latency_ms=latency_ms,
        )

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        """调用同一模型并返回业务模块自行校验的 JSON 对象。

        Args:
            system_prompt: 指定摘要或知识提炼职责及输出结构的系统提示词。
            user_prompt: 当前业务上下文。

        Returns:
            已完成容错解析的 JSON 字典；字段合法性由调用方 Service 校验。
        """

        result, _ = self._complete_json(system_prompt, user_prompt)
        return result

    def _complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict, int]:
        """执行 OpenAI 兼容请求并返回解析结果和累计耗时。

        模型服务偶尔会在第二次生成时返回代码块、前置解释或截断内容。
        首次响应无法解析时只重试一次，并降低温度、追加严格输出约束；网络
        错误和 HTTP 错误不在此处无限重试，避免客服点击“换一个”后堆积请求。
        """

        if not self.base_url or not self.api_key or not self.model_name:
            raise RuntimeError(
                "AI 模型未配置，请设置 AI_MODEL_BASE_URL、AI_MODEL_API_KEY 和 AI_MODEL_NAME"
            )
        total_latency_ms = 0
        last_parse_error: ValueError | None = None
        for attempt in range(2):
            retry_suffix = (
                "\n\n上一次输出无法解析。现在只输出一个完整 JSON 对象，"
                "不要输出 Markdown、思考过程、前后解释或截断内容。"
                if attempt
                else ""
            )
            body = json.dumps(
                {
                    "model": self.model_name,
                    "temperature": 0.0 if attempt else 0.2,
                    # 强制 JSON 输出（部分模型如 DeepSeek 支持，GPT-4o 也支持）
                    # 如果模型不支持，会 fallback 到普通文本，解析器仍会做容错提取。
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt + retry_suffix},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = Request(
                _completion_url(self.base_url),
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            started = time.perf_counter()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                # 模型服务返回 HTTP 错误（如 401 未授权、429 限流、500 内部错误）
                # 上游可能在正文回显密钥、提示词或客户消息，不向 API 或日志透传。
                raise RuntimeError(f"模型服务 HTTP {exc.code}，请核验服务配置与配额") from None
            except URLError as exc:
                # 网络不通、超时等
                raise RuntimeError("无法连接模型服务，请核验网络与服务配置") from None

            total_latency_ms += round((time.perf_counter() - started) * 1000)
            try:
                # 提取 choices[0].message.content；兼容文本块数组，不读取思考字段。
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError("模型返回结构异常，请核验接口兼容性") from None

            try:
                return _extract_json_object(content), total_latency_ms
            except ValueError as exc:
                last_parse_error = exc
                if attempt == 0:
                    continue
                raise last_parse_error from exc

        # range(2) 总会在成功或异常时返回/抛出；保留显式分支便于静态检查器理解。
        raise last_parse_error or ValueError("模型返回内容无法解析为 JSON 对象，请重试")
