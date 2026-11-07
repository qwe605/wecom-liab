"""跨层共享的稳定标识与检索词转换。"""

from __future__ import annotations

import re


def parse_public_conversation_id(value: str | int) -> int:
    """将 ``conv_123`` 或正整数转换为数据库会话编号。

    Raises:
        ValueError: 输入不是有效的正整数会话编号。
    """

    text = str(value or "").strip()
    if text.startswith("conv_"):
        text = text[5:]
    if not text.isdigit() or int(text) <= 0:
        raise ValueError("无效的 conversation_id")
    return int(text)


def search_terms(text: str) -> set[str]:
    """从自然语言文本中提取英文型号词和中文二元组。"""

    normalized = text.lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9._+-]{1,}", normalized))
    cjk = "".join(re.findall(r"[一-鿿]", normalized))
    terms.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return {item for item in terms if item}
