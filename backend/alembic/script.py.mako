"""Alembic 迁移脚本模板。"""
revision: str = "${up_revision}"
down_revision: str | None = "${down_revision}"
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    """升级到本版本。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回滚到上一版本。"""
    ${downgrades if downgrades else "pass"}
