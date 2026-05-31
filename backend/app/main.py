import asyncio
import base64
import datetime
import logging
import os
import shutil
import sys
import uuid
from typing import List, Optional

# 1. Temporarily remove the project root from sys.path if it is present
# so that python does not load '/Users/abhisheksharma/Desktop/purplle/app.py' as 'app'.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
while _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)

# 2. Make sure backend root is at index 0
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
while _BACKEND_ROOT in sys.path:
    sys.path.remove(_BACKEND_ROOT)
sys.path.insert(0, _BACKEND_ROOT)

# 3. Add project root to the very end of sys.path so that pipeline remains importable without shadowing 'app'
sys.path.append(_PROJECT_ROOT)
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError, DBAPIError

from app.database import get_db, init_db, DBEvent, DBVideoProcessing
from app.models import (
    EventIngest, IngestResponse, MetricsResponse, FunnelResponse,
    HeatmapResponse, HeatmapZone, AnomaliesResponse, HealthResponse
)
from app.ingestion import ingest_batch_events
from app.metrics import calculate_store_metrics, calculate_store_heatmap
from app.funnel import calculate_store_funnel
from app.anomalies import detect_store_anomalies
from app.health import check_system_health
from app.websocket import ws_manager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("store_intelligence.main")

app = FastAPI(
    title="AI Retail Intelligence Platform API",
    description="Production-grade real-time video analytics and retail intelligence dashboard.",
    version="1.0.0"
)

# Enable CORS for frontend integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory configurations
UPLOAD_DIR = "./uploads"

@app.on_event("startup")
async def startup():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    await init_db()
    logger.info("Application directories initialized. SQLite schema migrated successfully.")

# Custom 503 Exception handlers to suppress database disconnections tracebacks
@app.exception_handler(OperationalError)
async def database_operational_handler(request: Request, exc: OperationalError):
    logger.error(f"Database Operational Error: {str(exc)}")
    return JSONResponse(
        status_code=503,
        content={"detail": "Service Temporarily Unavailable: Database connection failed. Please retry shortly."}
    )

@app.exception_handler(DBAPIError)
async def database_api_handler(request: Request, exc: DBAPIError):
    logger.error(f"Database API Error: {str(exc)}")
    return JSONResponse(
        status_code=503,
        content={"detail": "Service Temporarily Unavailable: Database write or query failed. Please retry shortly."}
    )

# --- WEB SOCKETS ROUTE ---
@app.websocket("/ws/stream/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await ws_manager.connect(websocket)
    try:
        # Send initial metrics on connect
        async with AsyncSessionLocal_scope() as db:
            metrics = await calculate_store_metrics(db, store_id)
            await ws_manager.send_personal_message({
                "type": "metrics_update",
                "metrics": metrics.dict()
            }, websocket)
            
        while True:
            # Keep connection open, listen for client heartbeat
            data = await websocket.receive_text()
            await websocket.send_json({"type": "ping", "status": "alive"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket session error: {str(e)}")
        ws_manager.disconnect(websocket)

# Scope helper for database inside WebSocket or thread tasks
from app.database import AsyncSessionLocal
class AsyncSessionLocal_scope:
    def __init__(self):
        self.session = AsyncSessionLocal()
    async def __aenter__(self):
        return self.session
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()

# --- API ENDPOINTS ---

@app.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(events: List[EventIngest], db: AsyncSession = Depends(get_db)):
    """
    Batch ingest up to 500 events. Deduplicates events based on event_id.
    Pushes real-time dashboard updates via WebSockets when new events arrive.
    """
    try:
        response = await ingest_batch_events(db, events)
        
        # Trigger live broadcast to dashboards if events were successfully written
        if response.processed > 0 and len(events) > 0:
            store_id = events[0].store_id
            
            # Compute updated metrics
            metrics = await calculate_store_metrics(db, store_id)
            funnel = await calculate_store_funnel(db, store_id)
            anomalies = await detect_store_anomalies(db, store_id)
            
            # Broadcast the latest intelligence state
            await ws_manager.broadcast_json({
                "type": "metrics_update",
                "metrics": metrics.dict(),
                "funnel": funnel.dict(),
                "anomalies": anomalies.dict(),
                "latest_event": events[-1].dict()  # Latest behavior trigger
            })
            
        return response
    except ValueError as val_ex:
        return JSONResponse(status_code=400, content={"detail": str(val_ex)})

@app.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
async def get_store_metrics(store_id: str, db: AsyncSession = Depends(get_db)):
    """
    Fetch rolling unique visitors, conversions, dwell times, queues, and occupants.
    All employee traffic is fully excluded from analytics.
    """
    return await calculate_store_metrics(db, store_id)

@app.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_store_funnel(store_id: str, db: AsyncSession = Depends(get_db)):
    """
    Calculates conversion funnels utilizing session sequential logic to avoid double-counting.
    """
    return await calculate_store_funnel(db, store_id)

@app.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_store_heatmap(store_id: str, db: AsyncSession = Depends(get_db)):
    """
    Compiles spatial shopper grid. Normalizes traffic densities (0.0 to 1.0) and assigns sample confidence tags.
    """
    return await calculate_store_heatmap(db, store_id)

@app.get("/stores/{store_id}/anomalies", response_model=AnomaliesResponse)
async def get_store_anomalies(store_id: str, db: AsyncSession = Depends(get_db)):
    """
    Audit active anomalies including register bottlenecks, stale frames, or conversion dips.
    """
    return await detect_store_anomalies(db, store_id)

@app.get("/health", response_model=HealthResponse)
async def get_health(db: AsyncSession = Depends(get_db)):
    """
    Service health probe and diagnostic logger.
    """
    return await check_system_health(db)

# --- VIDEO PROCESSING FILE UPLOAD AND TRIGGER ---

@app.post("/video/upload")
async def upload_cctv_video(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...), 
    db: AsyncSession = Depends(get_db)
):
    video_id = str(uuid.uuid4())
    filename = f"{video_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    # Save the uploaded file locally
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # 1. Reset all previous analytics completely from the database
    from sqlalchemy import delete
    await db.execute(delete(DBEvent))
    await db.execute(delete(DBVideoProcessing))
    await db.commit()

    # 2. Broadcast reset payload to active WebSockets to instantly clear all dashboards
    await ws_manager.broadcast_json({
        "type": "live_frame",
        "video_id": video_id,
        "frame": None,
        "progress": 0.0,
        "metrics": {
            "store_id": "STORE_BLR_002",
            "unique_visitors": 0,
            "conversion_rate": 0.0,
            "avg_dwell_time_seconds": 0.0,
            "queue_depth": 0,
            "abandonment_rate": 0.0,
            "active_visitors": 0,
            "realtime_timestamp": datetime.datetime.utcnow()
        },
        "funnel": {
            "store_id": "STORE_BLR_002",
            "stages": [
                { "stage_name": "1. Store Entry", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 },
                { "stage_name": "2. Zone Browsing", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 },
                { "stage_name": "3. Queue Joined", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 },
                { "stage_name": "4. Checkout Purchase", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 }
            ]
        },
        "heatmap": {
            "store_id": "STORE_BLR_002",
            "zones": [
                { "zone_id": "SKINCARE", "visit_frequency": 0, "avg_dwell_ms": 0.0, "normalized_value": 0.0, "confidence_flag": "low" },
                { "zone_id": "COSMETICS", "visit_frequency": 0, "avg_dwell_ms": 0.0, "normalized_value": 0.0, "confidence_flag": "low" },
                { "zone_id": "BILLING_QUEUE", "visit_frequency": 0, "avg_dwell_ms": 0.0, "normalized_value": 0.0, "confidence_flag": "low" }
            ]
        },
        "anomalies": {
            "store_id": "STORE_BLR_002",
            "anomalies": []
        },
        "latest_event": None
    })
        
    # 3. Insert entry for the new video in processing table
    proc = DBVideoProcessing(
        video_id=video_id,
        filename=file.filename,
        status="pending",
        progress=0.0
    )
    db.add(proc)
    await db.commit()
    
    # 4. Register pipeline trigger inside background task
    from pipeline.detect import run_video_pipeline
    background_tasks.add_task(run_video_pipeline, video_id, file_path, "STORE_BLR_002", "CAM_ENTRY_01")
    
    return {"video_id": video_id, "status": "pending", "message": "Video uploaded. Processing initiated."}

@app.get("/video/status/{video_id}")
async def get_video_status(video_id: str, db: AsyncSession = Depends(get_db)):
    q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
    res = await db.execute(q)
    proc = res.scalar()
    
    if not proc:
        return JSONResponse(status_code=404, content={"detail": "Video tracking record not found."})
        
    return {
        "video_id": proc.video_id,
        "filename": proc.filename,
        "status": proc.status,
        "progress": proc.progress,
        "error_message": proc.error_message,
        "updated_at": proc.updated_at
    }

@app.post("/video/reset")
async def reset_cctv_system(db: AsyncSession = Depends(get_db)):
    """
    Halts active background tasks by deleting all processing tasks,
    wipes the SQLite database cleanly, and broadcasts a WebSocket zero-state payload.
    """
    from sqlalchemy import delete
    await db.execute(delete(DBEvent))
    await db.execute(delete(DBVideoProcessing))
    await db.commit()

    # Broadcast reset payload to active WebSockets to instantly clear all dashboards
    await ws_manager.broadcast_json({
        "type": "live_frame",
        "video_id": None,
        "frame": None,
        "progress": 0.0,
        "metrics": {
            "store_id": "STORE_BLR_002",
            "unique_visitors": 0,
            "conversion_rate": 0.0,
            "avg_dwell_time_seconds": 0.0,
            "queue_depth": 0,
            "abandonment_rate": 0.0,
            "active_visitors": 0,
            "realtime_timestamp": datetime.datetime.utcnow()
        },
        "funnel": {
            "store_id": "STORE_BLR_002",
            "stages": [
                { "stage_name": "1. Store Entry", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 },
                { "stage_name": "2. Zone Browsing", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 },
                { "stage_name": "3. Queue Joined", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 },
                { "stage_name": "4. Checkout Purchase", "count": 0, "percentage": 0.0, "dropoff_percentage": 0 }
            ]
        },
        "heatmap": {
            "store_id": "STORE_BLR_002",
            "zones": [
                { "zone_id": "SKINCARE", "visit_frequency": 0, "avg_dwell_ms": 0.0, "normalized_value": 0.0, "confidence_flag": "low" },
                { "zone_id": "COSMETICS", "visit_frequency": 0, "avg_dwell_ms": 0.0, "normalized_value": 0.0, "confidence_flag": "low" },
                { "zone_id": "BILLING_QUEUE", "visit_frequency": 0, "avg_dwell_ms": 0.0, "normalized_value": 0.0, "confidence_flag": "low" }
            ]
        },
        "anomalies": {
            "store_id": "STORE_BLR_002",
            "anomalies": []
        },
        "latest_event": None
    })

    return {"status": "success", "message": "System reset successfully."}
