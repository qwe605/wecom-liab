"""下载并动态量化 bge-small-zh-v1.5 为本地 ONNX INT8 目录。

使用方式：
    python deploy/prepare_bge_small_int8.py /opt/kefubao/models/bge-small-zh-int8

完成后把 ``KEFUBAO_LIGHTWEIGHT_EMBEDDING_MODEL_PATH`` 指向输出目录。脚本只在
部署阶段运行，生产向量服务不会在线量化或修改模型文件。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


MAX_SEQUENCE_LENGTH = 256


def _limit_tokenizer_context(model_directory: Path) -> None:
    """把 FastEmbed 实际读取的 tokenizer 上限固定为 256 Token。

    FastEmbed 从 ``tokenizer_config.json`` 初始化截断长度；仅在业务代码中按
    字符裁剪不能严格等价于 Token 上限，因此部署产物必须直接固化该配置。

    Args:
        model_directory: 已复制完成的本地模型目录。

    Raises:
        RuntimeError: 模型包缺少 tokenizer 配置或配置不是 JSON 对象。
    """

    config_path = model_directory / "tokenizer_config.json"
    if not config_path.exists():
        raise RuntimeError("模型仓库缺少 tokenizer_config.json")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("tokenizer_config.json 格式无效")
    payload["model_max_length"] = MAX_SEQUENCE_LENGTH
    payload["max_length"] = MAX_SEQUENCE_LENGTH
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare(output_directory: str) -> Path:
    """下载 Hugging Face 上的 Qdrant ONNX 产物并动态量化为 QInt8。

    此处的 ``Qdrant/...`` 只是模型文件仓库名，不安装、不启动 Qdrant
    向量数据库，也不增加任何 Qdrant 运行时依赖。

    Args:
        output_directory: 独立 INT8 模型输出目录，必须与下载缓存分离。

    Returns:
        已生成模型目录的绝对路径。

    Raises:
        RuntimeError: 缺少部署依赖或源 ONNX 文件不存在。
        FileExistsError: 输出目录已存在，防止覆盖线上模型。
    """

    try:
        from huggingface_hub import snapshot_download
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:
        raise RuntimeError("请先安装 requirements-embedding.txt") from exc

    target = Path(output_directory).expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"输出目录已存在：{target}")
    source = Path(snapshot_download("Qdrant/bge-small-zh-v1.5")).resolve()
    source_model = source / "model_optimized.onnx"
    if not source_model.exists():
        raise RuntimeError(f"模型仓库缺少 {source_model.name}")

    shutil.copytree(source, target)
    target_model = target / "model_optimized.onnx"
    target_model.unlink()
    quantize_dynamic(
        model_input=str(source_model),
        model_output=str(target_model),
        weight_type=QuantType.QInt8,
    )
    _limit_tokenizer_context(target)
    return target


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python deploy/prepare_bge_small_int8.py <输出目录>")
    print(prepare(sys.argv[1]))
