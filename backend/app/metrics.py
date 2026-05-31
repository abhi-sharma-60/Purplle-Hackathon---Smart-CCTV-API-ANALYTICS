import datetime
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import DBEvent
from app.models import MetricsResponse, HeatmapResponse, HeatmapZone

async def calculate_store_metrics(db: AsyncSession, store_id: str) -> MetricsResponse:
    # 1. Total Unique Visitors (excluding staff)
    # We count unique visitor_ids that have had any event in the store
    v_query = select(func.count(func.distinct(DBEvent.visitor_id))).where(
        DBEvent.store_id == store_id,
        DBEvent.is_staff == False
    )
    res = await db.execute(v_query)
    unique_visitors = res.scalar() or 0

    # 2. Conversion Rate (visitors who purchased / total unique visitors)
    # A visitor purchased if they have a 'PURCHASE' event.
    p_query = select(func.count(func.distinct(DBEvent.visitor_id))).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type == "PURCHASE",
        DBEvent.is_staff == False
    )
    res = await db.execute(p_query)
    purchased_visitors = res.scalar() or 0
    
    conversion_rate = 0.0
    if unique_visitors > 0:
        conversion_rate = round((purchased_visitors / unique_visitors) * 100.0, 1)

    # 3. Average Dwell Time (in seconds, from ZONE_DWELL events)
    d_query = select(func.avg(DBEvent.dwell_ms)).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type == "ZONE_DWELL",
        DBEvent.is_staff == False
    )
    res = await db.execute(d_query)
    avg_dwell_ms = res.scalar() or 0.0
    avg_dwell_time_seconds = round(avg_dwell_ms / 1000.0, 1)

    # 4. Queue Depth (visitors currently in billing queue)
    # To determine who is currently in the queue, we check the latest billing-queue event for each visitor.
    # If the latest queue event is JOIN, they are in the queue. If it is ABANDON or they exited, they are not.
    # Let's run a query to get each visitor's last billing event.
    subq = select(
        DBEvent.visitor_id,
        DBEvent.event_type,
        func.row_number().over(
            partition_by=DBEvent.visitor_id,
            order_by=DBEvent.timestamp.desc()
        ).label("rn")
    ).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type.in_(["BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "PURCHASE", "EXIT"]),
        DBEvent.is_staff == False
    ).subquery()

    q_query = select(func.count(subq.c.visitor_id)).where(
        subq.c.rn == 1,
        subq.c.event_type == "BILLING_QUEUE_JOIN"
    )
    res = await db.execute(q_query)
    queue_depth = res.scalar() or 0

    # 5. Abandonment Rate (queue abandons / total queue joins)
    joins_query = select(func.count(DBEvent.event_id)).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type == "BILLING_QUEUE_JOIN",
        DBEvent.is_staff == False
    )
    res = await db.execute(joins_query)
    total_joins = res.scalar() or 0

    abandons_query = select(func.count(DBEvent.event_id)).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type == "BILLING_QUEUE_ABANDON",
        DBEvent.is_staff == False
    )
    res = await db.execute(abandons_query)
    total_abandons = res.scalar() or 0

    abandonment_rate = 0.0
    if total_joins > 0:
        abandonment_rate = round((total_abandons / total_joins) * 100.0, 1)

    # 6. Active Visitors (occupancy)
    # Visitors whose latest overall event was not 'EXIT' AND occurred within the last 5 minutes (for short video runs, we can broaden this or check if their last event was not EXIT).
    # Since video processing can be fast or historical, let's say: any visitor whose last event is NOT 'EXIT'.
    # To keep it realistic for live updates, we check if their last event is NOT 'EXIT' and occurred in the last 60 seconds of the latest timestamp in DB.
    max_t_query = select(func.max(DBEvent.timestamp)).where(DBEvent.store_id == store_id)
    res = await db.execute(max_t_query)
    max_timestamp = res.scalar()

    active_visitors = 0
    if max_timestamp:
        active_threshold = max_timestamp - datetime.timedelta(seconds=15)
        
        # Last event per visitor
        subq_active = select(
            DBEvent.visitor_id,
            DBEvent.event_type,
            DBEvent.timestamp,
            func.row_number().over(
                partition_by=DBEvent.visitor_id,
                order_by=DBEvent.timestamp.desc()
            ).label("rn")
        ).where(
            DBEvent.store_id == store_id,
            DBEvent.is_staff == False
        ).subquery()

        active_query = select(func.count(subq_active.c.visitor_id)).where(
            subq_active.c.rn == 1,
            subq_active.c.event_type != "EXIT",
            subq_active.c.timestamp >= active_threshold
        )
        res = await db.execute(active_query)
        active_visitors = res.scalar() or 0

    # Ensure queue depth doesn't exceed active occupancy
    if queue_depth > unique_visitors:
        queue_depth = max(0, unique_visitors)

    return MetricsResponse(
        store_id=store_id,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_time_seconds=avg_dwell_time_seconds,
        queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
        active_visitors=active_visitors,
        realtime_timestamp=max_timestamp or datetime.datetime.utcnow()
    )

async def calculate_store_heatmap(db: AsyncSession, store_id: str) -> HeatmapResponse:
    # 1. Fetch visit frequency (entries) per zone
    freq_q = select(
        DBEvent.zone_id,
        func.count(DBEvent.event_id).label("freq")
    ).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type.in_(["ZONE_ENTER", "BILLING_QUEUE_JOIN"]),
        DBEvent.is_staff == False
    ).group_by(DBEvent.zone_id)
    
    freq_res = await db.execute(freq_q)
    freq_rows = freq_res.all()
    freq_map = {r.zone_id: r.freq for r in freq_rows if r.zone_id}

    # 2. Fetch average dwell per zone from ZONE_DWELL events
    dwell_q = select(
        DBEvent.zone_id,
        func.avg(DBEvent.dwell_ms).label("dwell")
    ).where(
        DBEvent.store_id == store_id,
        DBEvent.event_type == "ZONE_DWELL",
        DBEvent.is_staff == False
    ).group_by(DBEvent.zone_id)
    
    dwell_res = await db.execute(dwell_q)
    dwell_rows = dwell_res.all()
    dwell_map = {r.zone_id: r.dwell for r in dwell_rows if r.zone_id}

    zones = []
    max_freq = max(freq_map.values()) if freq_map else 1
    all_zone_ids = set(freq_map.keys()).union(dwell_map.keys())
    
    for zone_id in all_zone_ids:
        freq = freq_map.get(zone_id, 0)
        dwell = dwell_map.get(zone_id, 0.0) or 0.0
        normalized = round(freq / max_freq, 2)
        
        # Confidence Calibration based on sample sizing
        if freq >= 5:
            conf = "high"
        elif freq >= 2:
            conf = "medium"
        else:
            conf = "low"
            
        zones.append(HeatmapZone(
            zone_id=zone_id,
            visit_frequency=freq,
            avg_dwell_ms=round(dwell, 1),
            normalized_value=normalized,
            confidence_flag=conf
        ))
        
    # Standard fallback mock placeholders if zones have zero events
    active_zones = {z.zone_id for z in zones}
    for default_zone in ["SKINCARE", "COSMETICS", "BILLING_QUEUE"]:
        if default_zone not in active_zones:
            zones.append(HeatmapZone(
                zone_id=default_zone,
                visit_frequency=0,
                avg_dwell_ms=0.0,
                normalized_value=0.0,
                confidence_flag="low"
            ))

    return HeatmapResponse(store_id=store_id, zones=zones)
