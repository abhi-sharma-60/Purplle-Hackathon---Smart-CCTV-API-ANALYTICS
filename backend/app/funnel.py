from typing import List
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import DBEvent
from app.models import FunnelResponse, FunnelStage

async def calculate_store_funnel(db: AsyncSession, store_id: str) -> FunnelResponse:
    # 1. Fetch all events for this store (excluding staff)
    q = select(
        DBEvent.visitor_id,
        DBEvent.event_type,
        DBEvent.zone_id
    ).where(
        DBEvent.store_id == store_id,
        DBEvent.is_staff == False
    )
    res = await db.execute(q)
    events = res.all()

    # 2. Track visitor milestones
    # Milestones per visitor_id
    # Stage 0: Enter Store (any event from visitor is a start, or 'ENTRY')
    # Stage 1: Zone Browse (entered Skincare, Cosmetics or any shopping zone)
    # Stage 2: Queue Join (joined billing queue)
    # Stage 3: Purchase (had a purchase event)
    visitor_milestones = {}

    for visitor_id, event_type, zone_id in events:
        if visitor_id not in visitor_milestones:
            visitor_milestones[visitor_id] = {
                "entered": True,
                "browsed": False,
                "queued": False,
                "purchased": False
            }
        
        milestones = visitor_milestones[visitor_id]
        
        if event_type == "ENTRY":
            milestones["entered"] = True
        elif event_type in ["ZONE_ENTER", "ZONE_DWELL"] and zone_id in ["SKINCARE", "COSMETICS"]:
            milestones["browsed"] = True
        elif event_type == "BILLING_QUEUE_JOIN":
            milestones["queued"] = True
        elif event_type == "PURCHASE":
            milestones["purchased"] = True

    # 3. Enforce sequential funnel progression:
    # Entry -> Browsed -> Queued -> Purchased
    # A customer can only proceed to a stage if they satisfied the previous stage.
    total_entry = 0
    total_browse = 0
    total_queue = 0
    total_purchase = 0

    for visitor_id, milestones in visitor_milestones.items():
        if milestones["entered"]:
            total_entry += 1
            if milestones["browsed"]:
                total_browse += 1
                if milestones["queued"]:
                    total_queue += 1
                    if milestones["purchased"]:
                        total_purchase += 1

    # Format into response
    stages = []
    
    # Stage 1: Store Entry
    p1 = 100.0 if total_entry > 0 else 0.0
    d1 = 0.0
    stages.append(FunnelStage(
        stage_name="1. Store Entry",
        count=total_entry,
        percentage=p1,
        dropoff_percentage=d1
    ))

    # Stage 2: Product Zone Browsing
    p2 = round((total_browse / total_entry * 100.0), 1) if total_entry > 0 else 0.0
    d2 = round((100.0 - p2), 1) if total_entry > 0 else 0.0
    stages.append(FunnelStage(
        stage_name="2. Zone Browsing",
        count=total_browse,
        percentage=p2,
        dropoff_percentage=d2
    ))

    # Stage 3: Billing Queue
    p3 = round((total_queue / total_entry * 100.0), 1) if total_entry > 0 else 0.0
    d3 = round((p2 - p3), 1) if total_entry > 0 else 0.0
    stages.append(FunnelStage(
        stage_name="3. Queue Joined",
        count=total_queue,
        percentage=p3,
        dropoff_percentage=d3
    ))

    # Stage 4: Purchase Completion
    p4 = round((total_purchase / total_entry * 100.0), 1) if total_entry > 0 else 0.0
    d4 = round((p3 - p4), 1) if total_entry > 0 else 0.0
    stages.append(FunnelStage(
        stage_name="4. Checkout Purchase",
        count=total_purchase,
        percentage=p4,
        dropoff_percentage=d4
    ))

    return FunnelResponse(store_id=store_id, stages=stages)
