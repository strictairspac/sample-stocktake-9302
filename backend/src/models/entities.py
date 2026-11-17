from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Text, UniqueConstraint, Boolean
from datetime import datetime, timezone
from src.config.database import Base
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True);email=Column(String(120),unique=True,nullable=False)
    password=Column(String(300),nullable=False);role=Column(String(20),nullable=False)
class Sample(Base):
    __tablename__='samples'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    title=Column(String(160),nullable=False);quantity=Column(Integer,nullable=False);location=Column(String(120),nullable=False)
    # Every quantity change bumps this counter. A stocktake remembers the value at freeze time,
    # so changing quantity away and back is still detected as a conflict.
    quantity_version=Column(Integer,nullable=False,default=0)
class Transfer(Base):
    __tablename__='transfers'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False);destination=Column(String(120),nullable=False)
    quantity=Column(Integer,nullable=False);note=Column(String(300),default='')
    invalidated=Column(Boolean,nullable=False,default=False)
    invalid_reason=Column(String(300),nullable=False,default='')
    invalidated_at=Column(DateTime(timezone=True),nullable=True)
    invalidated_by_count_id=Column(Integer,ForeignKey('inventory_counts.id'),nullable=True)
class Trial(Base):
    __tablename__='trials'
    id=Column(Integer,primary_key=True);name=Column(String(120),nullable=False)
    transfer_id=Column(Integer,ForeignKey('transfers.id'),nullable=False);timeout_ms=Column(Integer,nullable=False)
    status=Column(String(20),default='queued',nullable=False);result=Column(JSON,nullable=True)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc));error=Column(Text,default='')
class TrialBalance(Base):
    __tablename__='trial_balances'
    trial_id=Column(Integer,ForeignKey('trials.id'),primary_key=True);remaining=Column(Integer,nullable=False);received=Column(Integer,nullable=False,default=0)
class Receipt(Base):
    __tablename__='receipts'
    id=Column(Integer,primary_key=True);trial_id=Column(Integer,ForeignKey('trials.id'),nullable=False)
    actor=Column(String(20),nullable=False);quantity=Column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('trial_id','actor'),)
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True);actor=Column(String(120),nullable=False);action=Column(String(160),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))


class InventoryCount(Base):
    """Whole-library stocktake batch. Freezes every sample at one instant; later additions are excluded."""
    __tablename__='inventory_counts'
    id=Column(Integer,primary_key=True);batch_code=Column(String(80),unique=True,nullable=False)
    status=Column(String(20),nullable=False,default='pending')
    note=Column(String(300),nullable=False,default='')
    created_by=Column(String(120),nullable=False,default='')
    frozen_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)
    posted_at=Column(DateTime(timezone=True),nullable=True)
    posted_by=Column(String(120),nullable=False,default='')
    failed_reason=Column(String(400),nullable=False,default='')
    post_attempts=Column(Integer,nullable=False,default=0)


class InventoryCountLine(Base):
    """One frozen sample inside a batch. frozen_quantity/frozen_version capture the freeze instant."""
    __tablename__='inventory_count_lines'
    id=Column(Integer,primary_key=True)
    count_id=Column(Integer,ForeignKey('inventory_counts.id'),nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False)
    sample_code=Column(String(80),nullable=False)
    sample_title=Column(String(160),nullable=False)
    frozen_quantity=Column(Integer,nullable=False)
    frozen_version=Column(Integer,nullable=False)
    physical_quantity=Column(Integer,nullable=True)
    result=Column(String(20),nullable=False,default='pending')
    note=Column(String(300),nullable=False,default='')
    __table_args__=(UniqueConstraint('count_id','sample_id'),)

