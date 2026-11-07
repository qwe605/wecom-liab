"""项目级领域异常。

异常类型集中定义，避免 Service 通过字符串约定 HTTP 状态。Controller 仍只负责
把领域异常映射为公开响应，不参与并发或审核业务判断。
"""


class ConcurrencyConflictError(RuntimeError):
    """资源版本已经变化，调用方必须重新读取后再提交。"""

