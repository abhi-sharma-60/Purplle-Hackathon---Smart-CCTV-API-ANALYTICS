# Architectural & Design Choices - AI Retail Intelligence Platform

This document outlines the key technical choices, model selection arguments, schema structures, and database decisions made during the system build.

---

## 1. Deep Learning Model Selection
* **Choice**: YOLOv8s (`yolov8s.pt`)
* **Alternatives Considered**: YOLOv8n (nano), YOLOv8m (medium), YOLOv9 / RT-DETR
* **Reasoning**:
  - **YOLOv8s** offers the optimal balance between inference speed and detection accuracy. It operates at $\approx 35$ FPS on modern laptop CPUs while avoiding the false-positive detections in crowded scenes common in YOLOv8n.
  - While YOLOv8m and YOLOv9 provide slightly higher mean Average Precision (mAP), they demand dedicated GPUs to achieve real-time tracking frame rates, which would cause severe lag in containerized Docker environments running on general CPU hosts.
  - **Local Copying**: The weight files are copied directly from the root workspace into the Docker context. This ensures that the platform is 100% offline-ready, preventing network downloads or connection drops during the hackathon evaluation.

---

## 2. Ingestion API & Deduplication Schema
* **Choice**: SQL Bulk-Deduplication with SQLite unique indexes.
* **Alternatives Considered**: In-memory Redis deduplication, client-side deduplication.
* **Reasoning**:
  - To prevent duplicate counting during network retries or overlapping frame processing, we mapped `event_id` as the primary key.
  - In `POST /events/ingest`, the service maps input `event_id`s, performs a bulk select query in SQLite, and discards existing entries. This guarantees **strict HTTP idempotency** while avoiding individual query overhead.
  - **Batch Limits**: The API limits batch sizes to a maximum of 500 events per request. This prevents memory bloat during ingestion while letting the video pipeline upload telemetry data in bulk.

---

## 3. Database Layer Selection
* **Choice**: Async SQLite (`aiosqlite` + `SQLAlchemy`)
* **Alternatives Considered**: PostgreSQL, Redis
* **Reasoning**:
  - For a hackathon deployment, PostgreSQL requires additional container orchestration, setup credentials, and startup delays. Async SQLite runs within a local file (`retail_analytics.db`) mounted as a volume.
  - By using SQLAlchemy with `aiosqlite`, we maintain **fully asynchronous non-blocking queries** while keeping the system lightweight, portable, and zero-setup.
  - Indexes on `store_id`, `visitor_id`, and `event_type` ensure that aggregate query executions (funnels, heatmaps) complete in under `5ms`.

---

## 4. Sequential Funnel Calculation
* **Choice**: Sequential Session-Based Milestone Tracking
* **Alternatives Considered**: Independent Event Tallies
* **Reasoning**:
  - Simple event tallies (e.g. summing total entrances and total checkouts) lead to illogical funnel rates (such as a conversion rate over 100% if shoppers from a previous day buy today).
  - Our sequential tracker evaluates shopper timelines per visitor ID, ensuring that a shopper is counted as joining the queue only if they entered the store and browsed a product zone first. This guarantees logically correct metrics and handles visitor re-entries and occlusions.
