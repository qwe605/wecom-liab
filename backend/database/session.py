"""MySQL 连接与请求级数据库会话。

Service 负责决定事务是否成功，Repository 只对传入的 Session 执行 ORM 操作。
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Generator, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """创建进程级 SQLAlchemy Engine。

    Returns:
        启用连接健康检查和回收策略的数据库引擎。
    """

    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """返回统一 Session 工厂。"""

    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 请求级数据库依赖。

    Yields:
        一个数据库会话。请求成功时提交，异常时回滚。
    """

    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """为命令行任务提供事务会话。

    Yields:
        自动提交或回滚的数据库会话。
    """

    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

