from sqlalchemy import String, JSON, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Reconciliation(Base):
    """外协窑对账复盘。

    外协只上报烧成件数与四舍五入到一位小数的良率，厂内总账稍后给出整批
    合格件数。revision 是整批数据的版本号：任何补报、总账修正都会递增，
    批准必须针对读取时的版本，避免批准过期结果。
    """
    __tablename__ = 'reconciliations'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    target: Mapped[float] = mapped_column(Double, default=93.0)
    ledger_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
    approved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    approved_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ReconciliationItem(Base):
    """一条外协窑上报。reported_yield 保存一位小数字符串，避免浮点误差。"""
    __tablename__ = 'reconciliation_items'
    __table_args__ = (UniqueConstraint('reconciliation_id', 'batch', name='uq_reconciliation_batch'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reconciliation_id: Mapped[int] = mapped_column(ForeignKey('reconciliations.id'), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    batch: Mapped[str] = mapped_column(String(40))
    line: Mapped[str] = mapped_column(String(40), default='')
    fired: Mapped[int] = mapped_column(Integer)
    reported_yield: Mapped[str] = mapped_column(String(10))
    supplied_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplied_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    supplied_by: Mapped[str | None] = mapped_column(String(150), nullable=True)


class ReconciliationApproval(Base):
    """每次批准留存的依据快照。后续补报或修正总账不会抹掉历史批准。"""
    __tablename__ = 'reconciliation_approvals'

    id: Mapped[int] = mapped_column(primary_key=True)
    reconciliation_id: Mapped[int] = mapped_column(ForeignKey('reconciliations.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    approved_at: Mapped[float] = mapped_column(Double)
    snapshot: Mapped[dict] = mapped_column(JSON)
