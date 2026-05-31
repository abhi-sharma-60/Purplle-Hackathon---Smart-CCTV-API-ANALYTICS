import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

# Core Event Schema
class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

class EventIngest(BaseModel):
    event_id: str = Field(..., description="UUID-v4 unique identifier")
    store_id: str = Field(..., description="Store unique identifier")
    camera_id: str = Field(..., description="Camera identifier")
    visitor_id: str = Field(..., description="Unique visitor tag (VIS_xxxx)")
    event_type: str = Field(..., description="ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY, PURCHASE")
    timestamp: datetime.datetime = Field(..., description="Timestamp of event in ISO format")
    zone_id: Optional[str] = Field(None, description="Affected retail zone")
    dwell_ms: Optional[int] = Field(None, description="Time spent in zone in ms")
    is_staff: bool = Field(False, description="Flag identifying store employees")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model prediction confidence")
    metadata: Optional[EventMetadata] = None

# Responses
class IngestSuccess(BaseModel):
    event_id: str
    status: str = "success"

class IngestFailure(BaseModel):
    event_id: str
    error: str

class IngestResponse(BaseModel):
    processed: int
    duplicates_skipped: int
    failures: List[IngestFailure] = []

class MetricsResponse(BaseModel):
    store_id: str
    unique_visitors: int
    conversion_rate: float  # Percentage: e.g. 15.4
    avg_dwell_time_seconds: float
    queue_depth: int
    abandonment_rate: float  # Percentage: e.g. 5.2
    active_visitors: int
    realtime_timestamp: datetime.datetime

class FunnelStage(BaseModel):
    stage_name: str
    count: int
    percentage: float
    dropoff_percentage: float

class FunnelResponse(BaseModel):
    store_id: str
    stages: List[FunnelStage]

class HeatmapZone(BaseModel):
    zone_id: str
    visit_frequency: int
    avg_dwell_ms: float
    normalized_value: float  # 0.0 to 1.0 (relative popularity)
    confidence_flag: str  # high, medium, low based on sample size

class HeatmapResponse(BaseModel):
    store_id: str
    zones: List[HeatmapZone]

class Anomaly(BaseModel):
    anomaly_id: str
    timestamp: datetime.datetime
    type: str  # QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE, STALE_FEED
    severity: str  # CRITICAL, WARNING, INFO
    description: str
    suggested_action: str

class AnomaliesResponse(BaseModel):
    store_id: str
    anomalies: List[Anomaly]

class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    last_event_timestamp: Optional[datetime.datetime] = None
    stale_feed_warnings: List[str] = []
