import datetime
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock
from sqlalchemy.exc import OperationalError

from app.main import app
from app.database import get_db, DBEvent

# Setup Pytest Asyncio marker
pytestmark = pytest.mark.asyncio

from app.database import init_db

@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()

@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

# 1. Test health check endpoint
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "warning", "degraded"]
    assert "database_connected" in data

# 2. Test event ingestion and idempotent deduplication
async def test_event_ingestion_and_deduplication(client):
    store_id = "STORE_BLR_002"
    event_id = str(uuid.uuid4())
    visitor_id = "VIS_test_1"
    
    event_payload = {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id,
        "event_type": "ENTRY",
        "timestamp": "2026-03-03T14:22:10Z",
        "zone_id": None,
        "dwell_ms": None,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": 1
        }
    }

    # First Ingest -> Success
    response1 = await client.post("/events/ingest", json=[event_payload])
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["processed"] == 1
    assert data1["duplicates_skipped"] == 0
    assert len(data1["failures"]) == 0

    # Second Ingest of same payload -> Handled as duplicate (idempotency check)
    response2 = await client.post("/events/ingest", json=[event_payload])
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["processed"] == 0
    assert data2["duplicates_skipped"] == 1
    assert len(data2["failures"]) == 0

# 3. Test event ingestion batch limit enforcement
async def test_event_ingestion_batch_limit(client):
    oversized_payload = []
    for i in range(505):  # Exceeds max 500 limit
        oversized_payload.append({
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_ENTRY_01",
            "visitor_id": f"VIS_{i}",
            "event_type": "ENTRY",
            "timestamp": "2026-03-03T14:22:10Z",
            "confidence": 0.9,
            "is_staff": False
        })
        
    response = await client.post("/events/ingest", json=oversized_payload)
    assert response.status_code == 400
    assert "exceeds maximum limit" in response.json()["detail"]

# 4. Test store metrics with staff exclusions
async def test_store_metrics_and_staff_exclusion(client):
    store_id = f"STORE_METRIC_{uuid.uuid4().hex[:6]}"
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    staff_id = f"VIS_STAFF_{uuid.uuid4().hex[:6]}"
    
    events = [
        # Customer Entry
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "CAM_01",
            "visitor_id": visitor_id,
            "event_type": "ENTRY",
            "timestamp": "2026-03-03T14:00:00Z",
            "is_staff": False,
            "confidence": 0.95
        },
        # Staff Entry (should be excluded from analytical volume metrics)
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "CAM_01",
            "visitor_id": staff_id,
            "event_type": "ENTRY",
            "timestamp": "2026-03-03T14:05:00Z",
            "is_staff": True,
            "confidence": 0.98
        },
        # Customer checkout purchase
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "CAM_01",
            "visitor_id": visitor_id,
            "event_type": "PURCHASE",
            "timestamp": "2026-03-03T14:10:00Z",
            "is_staff": False,
            "confidence": 0.91
        }
    ]

    # Ingest events
    ingest_res = await client.post("/events/ingest", json=events)
    assert ingest_res.status_code == 200

    # Query metrics
    metrics_res = await client.get(f"/stores/{store_id}/metrics")
    assert metrics_res.status_code == 200
    metrics = metrics_res.json()

    # Staff is excluded -> unique_visitors count should be exactly 1
    assert metrics["unique_visitors"] == 1
    # Customer purchased -> conversion rate should be 100%
    assert metrics["conversion_rate"] == 100.0

# 5. Test sequential funnel analytics
async def test_store_funnel_calculations(client):
    store_id = f"STORE_FUNNEL_{uuid.uuid4().hex[:6]}"
    v1 = f"VIS_F1_{uuid.uuid4().hex[:6]}" # Reaches entry only
    v2 = f"VIS_F2_{uuid.uuid4().hex[:6]}" # Reaches zone browse
    v3 = f"VIS_F3_{uuid.uuid4().hex[:6]}" # Reaches purchase checkout
    
    events = [
        # Shopper 1
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v1, "event_type": "ENTRY", "timestamp": "2026-03-03T14:00:00Z", "is_staff": False, "confidence": 0.9},
        
        # Shopper 2
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v2, "event_type": "ENTRY", "timestamp": "2026-03-03T14:01:00Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v2, "event_type": "ZONE_ENTER", "zone_id": "SKINCARE", "timestamp": "2026-03-03T14:02:00Z", "is_staff": False, "confidence": 0.9},
        
        # Shopper 3
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v3, "event_type": "ENTRY", "timestamp": "2026-03-03T14:02:00Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v3, "event_type": "ZONE_ENTER", "zone_id": "COSMETICS", "timestamp": "2026-03-03T14:03:00Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v3, "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-03-03T14:04:00Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v3, "event_type": "PURCHASE", "timestamp": "2026-03-03T14:05:00Z", "is_staff": False, "confidence": 0.9}
    ]

    await client.post("/events/ingest", json=events)

    funnel_res = await client.get(f"/stores/{store_id}/funnel")
    assert funnel_res.status_code == 200
    funnel = funnel_res.json()["stages"]

    # Entry = 3
    assert funnel[0]["count"] == 3
    assert funnel[0]["percentage"] == 100.0
    
    # Browsed = 2 (v2, v3)
    assert funnel[1]["count"] == 2
    assert funnel[1]["percentage"] == 66.7
    
    # Queue = 1 (v3)
    assert funnel[2]["count"] == 1
    assert funnel[2]["percentage"] == 33.3

    # Purchase = 1 (v3)
    assert funnel[3]["count"] == 1
    assert funnel[3]["percentage"] == 33.3

# 6. Test active anomalies alerts (dead zones and queue spikes)
async def test_store_anomalies_alerts(client):
    store_id = f"STORE_ANOMALY_{uuid.uuid4().hex[:6]}"
    v1 = f"VIS_A1_{uuid.uuid4().hex[:6]}"
    v2 = f"VIS_A2_{uuid.uuid4().hex[:6]}"
    v3 = f"VIS_A3_{uuid.uuid4().hex[:6]}"
    v4 = f"VIS_A4_{uuid.uuid4().hex[:6]}"
    
    events = [
        # Customer Entries
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v1, "event_type": "ENTRY", "timestamp": "2026-03-03T14:00:00Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v2, "event_type": "ENTRY", "timestamp": "2026-03-03T14:00:10Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v3, "event_type": "ENTRY", "timestamp": "2026-03-03T14:00:20Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v4, "event_type": "ENTRY", "timestamp": "2026-03-03T14:00:30Z", "is_staff": False, "confidence": 0.9},
        
        # 4 queue joins -> Bottleneck spike anomaly
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v1, "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-03-03T14:02:00Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v2, "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-03-03T14:02:10Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v3, "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-03-03T14:02:20Z", "is_staff": False, "confidence": 0.9},
        {"event_id": str(uuid.uuid4()), "store_id": store_id, "camera_id": "CAM_01", "visitor_id": v4, "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-03-03T14:02:30Z", "is_staff": False, "confidence": 0.9}
    ]

    await client.post("/events/ingest", json=events)

    anom_res = await client.get(f"/stores/{store_id}/anomalies")
    assert anom_res.status_code == 200
    anomalies = anom_res.json()["anomalies"]
    
    types = [a["type"] for a in anomalies]
    
    # Assert critical queue bottleneck was triggered
    assert "QUEUE_SPIKE" in types
    
    # Assert dead zones were identified since no one browsed skincare
    assert "DEAD_ZONE" in types

# 7. Test DB Operational Error handling (returns graceful 503 instead of Python stack trace)
async def test_database_operational_error_graceful_503(client):
    # Mocking database connection error during metrics request
    with patch("app.main.calculate_store_metrics", side_effect=OperationalError("mock query error", params={}, orig=Exception("DB Down"))):
        response = await client.get("/stores/STORE_BLR_002/metrics")
        assert response.status_code == 503
        data = response.json()
        assert "Service Temporarily Unavailable" in data["detail"]
        assert "traceback" not in response.text
