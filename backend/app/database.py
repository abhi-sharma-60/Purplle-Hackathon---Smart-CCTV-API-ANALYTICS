import datetime
import uuid
from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

DATABASE_URL = "sqlite+aiosqlite:///./retail_analytics.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

class DBEvent(Base):
    __tablename__ = "events"

    event_id = Column(String, primary_key=True, index=True)
    store_id = Column(String, index=True, nullable=False)
    camera_id = Column(String, index=True, nullable=False)
    visitor_id = Column(String, index=True, nullable=False)
    event_type = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    zone_id = Column(String, nullable=True)
    dwell_ms = Column(Integer, nullable=True)
    is_staff = Column(Boolean, default=False, nullable=False)
    confidence = Column(Float, nullable=False)
    metadata_json = Column(JSON, nullable=True)

class DBVideoProcessing(Base):
    __tablename__ = "video_processing"

    video_id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending, processing, completed, failed
    progress = Column(Float, default=0.0, nullable=False)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
