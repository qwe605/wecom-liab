"""效果指标权限和窗口编排服务。"""

from backend.database.repositories.effect_repository import EffectRepository


class EffectService:
    """仅向 reviewer/admin 提供内部效果指标。"""

    def __init__(self, repository: EffectRepository):
        """注入只读效果仓储。"""

        self.repository = repository

    def overview(self, staff_userid: str, days: int) -> dict:
        """校验管理权限并返回限定时间窗口的指标。"""

        user = self.repository.get_user(staff_userid)
        if not user or not user["is_enabled"] or user["role"] not in {"reviewer", "admin"}:
            raise PermissionError("当前账号没有效果指标查看权限")
        return self.repository.overview(max(1, min(int(days), 365)))

