import asyncio
import base64
import datetime
import logging
import os
import numpy as np
import cv2
import supervision as sv
from ultralytics import YOLO
from sqlalchemy.future import select

from app.database import AsyncSessionLocal, DBVideoProcessing, DBEvent
from app.websocket import ws_manager
from app.metrics import calculate_store_metrics, calculate_store_heatmap
from app.funnel import calculate_store_funnel
from app.anomalies import detect_store_anomalies
from app.ingestion import ingest_batch_events
from app.models import EventIngest
from pipeline.zones import StoreZoneManager
from pipeline.tracker import VisitorSessionTracker

logger = logging.getLogger("store_intelligence.pipeline.detect")

async def run_video_pipeline(video_id: str, video_path: str, store_id: str, camera_id: str):
    logger.info(f"Kicking off AI retail video tracking pipeline for video_id={video_id}...")
    
    # 1. Update status to 'processing' in database
    async with AsyncSessionLocal() as db:
        q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
        res = await db.execute(q)
        proc = res.scalar()
        if proc:
            proc.status = "processing"
            proc.progress = 0.0
            await db.commit()
            
    # 2. Open Video Capture using OpenCV
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        err_msg = f"Failed to open video source: {video_path}"
        logger.error(err_msg)
        async with AsyncSessionLocal() as db:
            q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
            res = await db.execute(q)
            proc = res.scalar()
            if proc:
                proc.status = "failed"
                proc.error_message = err_msg
                await db.commit()
        return

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    
    logger.info(f"Video metadata: Res={width}x{height}, FPS={fps}, TotalFrames={total_frames}")

    # 3. Initialize custom analytics engines
    zone_manager = StoreZoneManager(width, height)
    tracker = VisitorSessionTracker(store_id, camera_id)
    
    # Standard Supervision Bounding Box Annotators
    box_annotator = sv.BoxAnnotator(
        thickness=2,
        color_lookup=sv.ColorLookup.TRACK
    )
    label_annotator = sv.LabelAnnotator(
        text_padding=4,
        text_scale=0.5,
        text_thickness=1,
        color_lookup=sv.ColorLookup.TRACK
    )

    # 4. Load YOLOv8 Model (reuse downloaded yolov8s.pt in root)
    model_path = "./yolov8s.pt"
    if not os.path.exists(model_path):
        model_path = "../yolov8s.pt"  # Docker path fallback
    if not os.path.exists(model_path):
        model_path = "yolov8s.pt"     # CLI local fallback
        
    try:
        model = YOLO(model_path)
    except Exception as ex:
        err_msg = f"Failed to load YOLO model from path {model_path}: {str(ex)}"
        logger.error(err_msg)
        async with AsyncSessionLocal() as db:
            q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
            res = await db.execute(q)
            proc = res.scalar()
            if proc:
                proc.status = "failed"
                proc.error_message = err_msg
                await db.commit()
        cap.release()
        return

    frame_idx = 0
    start_time = datetime.datetime.utcnow()  # Aligned to real-time clock for accurate diagnostics

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
                
            frame_idx += 1
            
            # Active video check to allow clean reset/abort when a new video is uploaded
            if frame_idx % 10 == 0:
                async with AsyncSessionLocal() as db_check:
                    q_check = select(DBVideoProcessing.video_id).where(DBVideoProcessing.video_id == video_id)
                    res_check = await db_check.execute(q_check)
                    if not res_check.scalar():
                        logger.warning(f"Video {video_id} has been wiped by a newer upload. Aborting pipeline task.")
                        break
            
            # Rate limit frames inside database status update (every 25 frames)
            if frame_idx % 25 == 0 or frame_idx == total_frames:
                progress = round((frame_idx / total_frames) * 100.0, 1)
                async with AsyncSessionLocal() as db:
                    q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
                    res = await db.execute(q)
                    proc = res.scalar()
                    if proc:
                        proc.progress = progress
                        await db.commit()
            
            # Run heavy YOLO tracking inference in a separate executor thread
            # to prevent blocking the FastAPI asyncio main event loop.
            results = await asyncio.to_thread(
                model.track,
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                verbose=False,
                conf=0.25
            )
            
            detections = sv.Detections.from_ultralytics(results[0])
            
            # Filter detections to ONLY include those that have a valid tracker_id
            if detections.tracker_id is None:
                detections = sv.Detections.empty()
            else:
                valid_mask = np.array([tid is not None and tid >= 0 for tid in detections.tracker_id], dtype=bool)
                detections = detections[valid_mask]
            
            # 5. Core logic updates: Zones, Lines and session states
            # Get line crossing lists
            crossed_in, crossed_out = zone_manager.line_zone.trigger(detections)
            
            # Compute simulated time matching video FPS
            simulated_time = start_time + datetime.timedelta(seconds=frame_idx / fps)
            
            # Process state transitions and get events
            frame_events = tracker.update_tracks(
                detections=detections,
                frame_time=simulated_time,
                zone_manager=zone_manager,
                crossed_in=crossed_in,
                crossed_out=crossed_out
            )
            
            # Save events to database directly in an async session
            if frame_events:
                models = [EventIngest(**e) for e in frame_events]
                async with AsyncSessionLocal() as db:
                    await ingest_batch_events(db, models)
                    await db.commit()

            # 6. Polished visual overlays
            # Draw standard zones and lines
            annotated_frame = zone_manager.draw_zones(frame.copy())
            
            # Draw Bounding boxes and labels (Green styling)
            if len(detections) > 0:
                annotated_frame = box_annotator.annotate(
                    scene=annotated_frame,
                    detections=detections
                )
                
                labels = [
                    f"Shopper #{tid} ({conf:.2f})"
                    for tid, conf in zip(detections.tracker_id, detections.confidence)
                ]
                annotated_frame = label_annotator.annotate(
                    scene=annotated_frame,
                    detections=detections,
                    labels=labels
                )
                
            # Draw HUD analytical overlays
            # Count current queue depth
            current_queue = int(sum(zone_manager.billing_queue_zone.trigger(detections)))
            current_skincare = int(sum(zone_manager.skincare_zone.trigger(detections)))
            current_cosmetics = int(sum(zone_manager.cosmetics_zone.trigger(detections)))
            
            # Add stylish glass-HUD background card at top-left
            cv2.rectangle(annotated_frame, (10, 10), (320, 150), (20, 20, 20), -1)
            cv2.rectangle(annotated_frame, (10, 10), (320, 150), (60, 60, 60), 2)
            
            cv2.putText(annotated_frame, "AURA INTEL LIVE HUD", (25, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (59, 130, 246), 2)
            cv2.putText(annotated_frame, f"Occupancy: {len(detections)} shoppers", (25, 65),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(annotated_frame, f"Skincare Dwellers: {current_skincare}", (25, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(annotated_frame, f"Billing Queue: {current_queue}", (25, 115),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (59, 130, 246), 1)
            
            # 7. Convert frame to Base64 JPEG for WebSocket broadcast
            # To maximize real-time performance, throttle streaming (broadcast every 2 frames ~12.5 FPS)
            if frame_idx % 2 == 0 or frame_idx == total_frames:
                # Resize slightly to minimize bandwidth without losing visual crispness
                resized_frame = cv2.resize(annotated_frame, (960, 540))
                _, buffer = cv2.imencode(".jpg", resized_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                base64_str = base64.b64encode(buffer).decode("utf-8")
                
                # Fetch rolling dashboard statistics to synchronize
                async with AsyncSessionLocal() as db:
                    metrics = await calculate_store_metrics(db, store_id)
                    funnel = await calculate_store_funnel(db, store_id)
                    heatmap = await calculate_store_heatmap(db, store_id)
                    anomalies = await detect_store_anomalies(db, store_id)
                
                # Send frame and dashboard metrics via WebSocket
                await ws_manager.broadcast_json({
                    "type": "live_frame",
                    "video_id": video_id,
                    "frame": f"data:image/jpeg;base64,{base64_str}",
                    "progress": round((frame_idx / total_frames) * 100.0, 1),
                    "metrics": metrics.dict(),
                    "funnel": funnel.dict(),
                    "heatmap": heatmap.dict(),
                    "anomalies": anomalies.dict(),
                    "latest_event": frame_events[-1] if frame_events else None
                })
                
            # Brief sleep to simulate real-time frame pacing (about 40ms for 25 FPS)
            await asyncio.sleep(0.04)

        # 8. Finished processing video file successfully
        logger.info(f"Video pipeline finished processing successfully for video_id={video_id}!")
        
        async with AsyncSessionLocal() as db:
            q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
            res = await db.execute(q)
            proc = res.scalar()
            if proc:
                proc.status = "completed"
                proc.progress = 100.0
                await db.commit()
                
            # Broadcast final completion status
            metrics = await calculate_store_metrics(db, store_id)
            funnel = await calculate_store_funnel(db, store_id)
            heatmap = await calculate_store_heatmap(db, store_id)
            anomalies = await detect_store_anomalies(db, store_id)
            await ws_manager.broadcast_json({
                "type": "processing_completed",
                "video_id": video_id,
                "metrics": metrics.dict(),
                "funnel": funnel.dict(),
                "heatmap": heatmap.dict(),
                "anomalies": anomalies.dict()
            })

    except Exception as run_ex:
        err_msg = f"Exception encountered in video tracking execution loop: {str(run_ex)}"
        logger.error(err_msg, exc_info=True)
        async with AsyncSessionLocal() as db:
            q = select(DBVideoProcessing).where(DBVideoProcessing.video_id == video_id)
            res = await db.execute(q)
            proc = res.scalar()
            if proc:
                proc.status = "failed"
                proc.error_message = err_msg
                await db.commit()
                
    finally:
        cap.release()
