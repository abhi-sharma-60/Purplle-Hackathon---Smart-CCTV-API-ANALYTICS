import datetime
from typing import Optional
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import DBEvent
from app.models import HealthResponse

async def check_system_health(db: AsyncSession) -> HealthResponse:
    db_connected = False
    last_event_t: Optional[datetime.datetime] = None
    warnings = []

    try:
        # 1. Test database connection
        res = await db.execute(select(1))
        if res.scalar() == 1:
            db_connected = True
        
        # 2. Get last event timestamp
        t_res = await db.execute(select(func.max(DBEvent.timestamp)))
        last_event_t = t_res.scalar()
        
        if last_event_t:
            seconds_since_last = (datetime.datetime.utcnow() - last_event_t).total_seconds()
            if seconds_since_last > 45.0:
                warnings.append(f"Feed inactivity warning: Last retail event was ingested {int(seconds_since_last)} seconds ago.")
        else:
            warnings.append("Stale feed warning: No camera events have been ingested into this store database yet.")
            
    except Exception as e:
        db_connected = False
        warnings.append(f"Database connection failure: {str(e)}")

    status = "healthy"
    if not db_connected:
        status = "degraded"
    elif any("warning" in w.lower() for w in warnings):
        status = "warning"

    return HealthResponse(
        status=status,
        database_connected=db_connected,
        last_event_timestamp=last_event_t,
        stale_feed_warnings=warnings
    )
