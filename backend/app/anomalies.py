import datetime
import uuid
from typing import List
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import DBEvent
from app.models import AnomaliesResponse, Anomaly
from app.metrics import calculate_store_metrics

async def detect_store_anomalies(db: AsyncSession, store_id: str) -> AnomaliesResponse:
    anomalies: List[Anomaly] = []
    
    # 1. Fetch current live metrics to perform quick checks
    metrics = await calculate_store_metrics(db, store_id)
    
    # 2. Query all active zones visited
    z_query = select(DBEvent.zone_id, func.count(DBEvent.event_id)).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type == "ZONE_ENTER",
        DBEvent.is_staff == False
    ).group_by(DBEvent.zone_id)
    res = await db.execute(z_query)
    zone_counts = dict(res.all())

    # --- ANOMALY 1: QUEUE SPIKES ---
    if metrics.queue_depth >= 4:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow(),
            type="QUEUE_SPIKE",
            severity="CRITICAL",
            description=f"Billing queue bottleneck detected! Current depth: {metrics.queue_depth} shoppers.",
            suggested_action="Deploy secondary cashier to POS counter immediately to reduce customer wait time."
        ))
    elif metrics.queue_depth >= 2:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow(),
            type="QUEUE_SPIKE",
            severity="WARNING",
            description=f"Billing queue is building. Depth: {metrics.queue_depth} shoppers.",
            suggested_action="Prepare auxiliary cashier for active checkout support."
        ))

    # --- ANOMALY 2: CONVERSION DROPS ---
    if metrics.unique_visitors >= 5 and metrics.conversion_rate < 10.0:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow(),
            type="CONVERSION_DROP",
            severity="CRITICAL",
            description=f"Store conversion rate has plummeted to {metrics.conversion_rate}% despite high traffic ({metrics.unique_visitors} unique shoppers).",
            suggested_action="Instruct sales associates to offer interactive product assistance in active zones."
        ))
    elif metrics.unique_visitors >= 3 and metrics.conversion_rate < 15.0:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow(),
            type="CONVERSION_DROP",
            severity="WARNING",
            description=f"Store conversion rate is below target. Current conversion: {metrics.conversion_rate}%.",
            suggested_action="Check for queue abandonment or pricing discrepancies at register."
        ))

    # --- ANOMALY 3: DEAD ZONES ---
    # Standard zones expected in our store
    expected_zones = ["SKINCARE", "COSMETICS"]
    if metrics.unique_visitors >= 4:
        for zone in expected_zones:
            count = zone_counts.get(zone, 0)
            if count == 0:
                anomalies.append(Anomaly(
                    anomaly_id=str(uuid.uuid4()),
                    timestamp=datetime.datetime.utcnow(),
                    type="DEAD_ZONE",
                    severity="WARNING",
                    description=f"Zone '{zone}' has received 0 shopper visits despite {metrics.unique_visitors} unique entries today.",
                    suggested_action=f"Inspect if aisle display for '{zone}' is blocked, or refresh product placements and lighting."
                ))

    # --- ANOMALY 4: STALE FEEDS ---
    # Check if the latest event in the store is older than 20 seconds
    max_t_query = select(func.max(DBEvent.timestamp)).where(DBEvent.store_id == store_id)
    res = await db.execute(max_t_query)
    max_timestamp = res.scalar()
    
    if max_timestamp:
        time_since_last_event = (datetime.datetime.utcnow() - max_timestamp).total_seconds()
        # For historical test run, we don't want to alert unless we are in a live streaming mode.
        # But to be robust, if we are in live stream, we can check if it is active.
        # Let's say: if time_since_last_event > 60.0 in simulated real-time mode:
        if time_since_last_event > 45.0:
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                timestamp=datetime.datetime.utcnow(),
                type="STALE_FEED",
                severity="CRITICAL",
                description=f"CCTV camera feed status warning: no events received in the last {int(time_since_last_event)} seconds.",
                suggested_action="Verify camera power supply, streaming network credentials, or re-establish RTSP capture process."
            ))
    else:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.utcnow(),
            type="STALE_FEED",
            severity="WARNING",
            description="No camera event data has been recorded for this store.",
            suggested_action="Start the CCTV pipeline tracking node to begin real-time data ingestion."
        ))

    return AnomaliesResponse(store_id=store_id, anomalies=anomalies)
