from typing import List, Set
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import DBEvent
from app.models import EventIngest, IngestResponse, IngestFailure

async def ingest_batch_events(db: AsyncSession, events: List[EventIngest]) -> IngestResponse:
    if not events:
        return IngestResponse(processed=0, duplicates_skipped=0, failures=[])
    
    # 1. Enforcement of batch size max 500
    if len(events) > 500:
        raise ValueError("Batch size exceeds maximum limit of 500 events")

    event_ids = [e.event_id for e in events]
    
    # 2. SQL deduplication - find existing event_ids in database
    q = select(DBEvent.event_id).where(DBEvent.event_id.in_(event_ids))
    result = await db.execute(q)
    existing_ids = set(result.scalars().all())

    to_insert: List[DBEvent] = []
    failures: List[IngestFailure] = []
    duplicates_skipped = 0
    
    seen_in_batch: Set[str] = set()

    for event in events:
        # Check if duplicated inside the database
        if event.event_id in existing_ids:
            duplicates_skipped += 1
            continue
        
        # Check if duplicated inside the batch itself
        if event.event_id in seen_in_batch:
            duplicates_skipped += 1
            continue
            
        seen_in_batch.add(event.event_id)

        try:
            db_event = DBEvent(
                event_id=event.event_id,
                store_id=event.store_id,
                camera_id=event.camera_id,
                visitor_id=event.visitor_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                zone_id=event.zone_id,
                dwell_ms=event.dwell_ms,
                is_staff=event.is_staff,
                confidence=event.confidence,
                metadata_json=event.metadata.dict() if event.metadata else None
            )
            to_insert.append(db_event)
        except Exception as ex:
            failures.append(IngestFailure(event_id=event.event_id, error=str(ex)))

    if to_insert:
        db.add_all(to_insert)
        await db.flush()  # Push to DB transaction
    
    processed = len(to_insert)
    
    return IngestResponse(
        processed=processed,
        duplicates_skipped=duplicates_skipped,
        failures=failures
    )
