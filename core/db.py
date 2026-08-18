"""数据库模型 - SQLite/MySQL via SQLModel"""
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session, select
from sqlalchemy import Column, Text
import json
import logging
import os

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


def _load_env_file_values() -> dict:
    """读取项目根目录 .env（项目无 python-dotenv，这里自实现）。"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    values: dict = {}
    try:
        for raw in open(env_path, encoding="utf-8", errors="ignore"):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return values


def _build_database_url() -> str:
    """数据库连接：优先 MySQL 环境变量，缺省回退 SQLite。"""
    env = _load_env_file_values()
    env.update(os.environ)
    host = env.get("MYSQL_HOST", "")
    port = env.get("MYSQL_PORT", "3306")
    user = env.get("MYSQL_USER", "")
    password = env.get("MYSQL_PASSWORD", "")
    dbname = env.get("MYSQL_DATABASE", "")
    if host and user and dbname:
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{dbname}?charset=utf8mb4"
    return env.get("DATABASE_URL", "sqlite:///account_manager.db")


DATABASE_URL = _build_database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class AccountModel(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    email: str = Field(index=True)
    password: str
    user_id: str = ""
    region: str = ""
    # WorkOS/OpenBlockLabs 的 wos-session 可能超过传统 VARCHAR(255)；
    # 使用 Text，避免注册成功后在保存账号阶段被数据库截断。
    token: str = Field(default="", sa_column=Column(Text))
    status: str = "registered"
    trial_end_time: int = 0
    cashier_url: str = Field(default="", sa_column=Column(Text))
    extra_json: str = Field(default="{}", sa_column=Column(Text))   # JSON 存储平台自定义字段
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, d: dict):
        self.extra_json = json.dumps(d, ensure_ascii=False)


class TaskLog(SQLModel, table=True):
    __tablename__ = "task_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str
    email: str
    status: str        # success | failed
    error: str = Field(default="", sa_column=Column(Text))
    detail_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utcnow)


class ProxyModel(SQLModel, table=True):
    __tablename__ = "proxies"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True)
    region: str = ""
    success_count: int = 0
    fail_count: int = 0
    is_active: bool = True
    last_checked: Optional[datetime] = None


def save_account(account) -> 'AccountModel':
    """从 base_platform.Account 存入数据库（同平台同邮箱则更新）"""
    with Session(engine) as session:
        existing = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == account.platform)
            .where(AccountModel.email == account.email)
        ).first()
        if existing:
            existing.password = account.password
            existing.user_id = account.user_id or ""
            existing.region = account.region or ""
            existing.token = account.token or ""
            existing.status = account.status.value
            existing.extra_json = json.dumps(account.extra or {}, ensure_ascii=False)
            existing.cashier_url = (account.extra or {}).get("cashier_url", "")
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        m = AccountModel(
            platform=account.platform,
            email=account.email,
            password=account.password,
            user_id=account.user_id or "",
            region=account.region or "",
            token=account.token or "",
            status=account.status.value,
            extra_json=json.dumps(account.extra or {}, ensure_ascii=False),
            cashier_url=(account.extra or {}).get("cashier_url", ""),
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        return m


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_account_token_column()
    _migrate_account_cashier_url_column()


def _migrate_account_token_column() -> None:
    """将旧数据库中的 accounts.token 扩容为 TEXT。

    create_all 不会修改已有表结构，而现有部署可能已经用 VARCHAR(255)
    建表，因此在启动时对 MySQL/MariaDB 做一次幂等迁移。SQLite 的 TEXT
    亲和类型不受 VARCHAR 长度限制，无需改表。
    """
    if engine.dialect.name != "mysql":
        return
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE accounts MODIFY COLUMN token TEXT NOT NULL"
            )
    except Exception as exc:
        logger.warning(f"accounts.token 扩容迁移失败: {exc}")


def _migrate_account_cashier_url_column() -> None:
    """将 accounts.cashier_url 扩容为 TEXT（Stripe 完整 URL 含 #fragment 很长）。"""
    if engine.dialect.name != "mysql":
        return
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE accounts MODIFY COLUMN cashier_url TEXT"
            )
    except Exception as exc:
        logger.warning(f"accounts.cashier_url 扩容迁移失败: {exc}")


def get_session():
    with Session(engine) as session:
        yield session


# 定时任务模型
class ScheduledTaskModel(SQLModel, table=True):
    __tablename__ = "scheduled_tasks"

    task_id: str = Field(primary_key=True)
    platform: str
    count: int = 1
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    interval_type: str = "minutes"  # minutes | hours
    interval_value: int = 30
    paused: bool = False
    extra_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, d: dict):
        self.extra_json = json.dumps(d, ensure_ascii=False)
