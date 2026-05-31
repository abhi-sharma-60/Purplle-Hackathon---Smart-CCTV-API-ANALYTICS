import datetime
import uuid
from typing import Dict, List, Any, Set
import numpy as np
import supervision as sv

class VisitorSessionTracker:
    def __init__(self, store_id: str, camera_id: str):
        self.store_id = store_id
        self.camera_id = camera_id
        
        # Shopper active state indexes:
        # visitor_id -> { "first_seen", "last_seen", "current_zone", "zone_enter_time", 
        #                 "in_queue", "queue_join_time", "has_entered", "has_exited", 
        #                 "history", "is_staff", "frame_count", "staff_zone_frames" }
        self.sessions: Dict[str, Dict[str, Any]] = {}
        
        # Track duplicate check for events generated in this execution run
        self.emitted_entries: Set[str] = set()
        self.emitted_exits: Set[str] = set()

    def update_tracks(
        self,
        detections: sv.Detections,
        frame_time: datetime.datetime,
        zone_manager: Any,
        crossed_in: np.ndarray,
        crossed_out: np.ndarray
    ) -> List[Dict[str, Any]]:
        events = []
        
        if detections.tracker_id is None:
            return events

        # Map detections to zone presences
        in_skincare = zone_manager.skincare_zone.trigger(detections)
        in_cosmetics = zone_manager.cosmetics_zone.trigger(detections)
        in_billing = zone_manager.billing_queue_zone.trigger(detections)
        in_staff_zone = zone_manager.staff_zone.trigger(detections)

        active_track_ids = set()

        for idx, tracker_id in enumerate(detections.tracker_id):
            visitor_id = f"VIS_{tracker_id}"
            active_track_ids.add(visitor_id)
            
            # Confidence score for the box
            confidence = float(detections.confidence[idx]) if detections.confidence is not None else 0.90
            
            # Initialize session state if not seen before
            if visitor_id not in self.sessions:
                self.sessions[visitor_id] = {
                    "first_seen": frame_time,
                    "last_seen": frame_time,
                    "current_zone": None,
                    "zone_enter_time": None,
                    "in_queue": False,
                    "queue_join_time": None,
                    "has_entered": False,
                    "has_exited": False,
                    "history": [],
                    "is_staff": False,
                    "frame_count": 0,
                    "staff_zone_frames": 0
                }
            
            s = self.sessions[visitor_id]
            s["last_seen"] = frame_time
            s["frame_count"] += 1
            
            # 1. Staff Exclusion Evaluation
            # If they reside inside the restricted cashier zone frequently, flag as staff
            if in_staff_zone[idx]:
                s["staff_zone_frames"] += 1
            
            # If visitor frame residency is high OR they stay in staff zone > 15% of their frames, exclude
            if s["frame_count"] >= 10:
                ratio = s["staff_zone_frames"] / s["frame_count"]
                if ratio > 0.15 or s["frame_count"] > 180:  # Present in >50% of our short video
                    s["is_staff"] = True

            # 2. ENTRY HANDLING
            # Trigger ENTRY if crossed line IN, or if seen for the first time
            triggered_entry = crossed_in[idx] or (not s["has_entered"] and s["frame_count"] >= 2)
            
            if triggered_entry and visitor_id not in self.emitted_entries:
                s["has_entered"] = True
                self.emitted_entries.add(visitor_id)
                
                # Check for Re-entry
                event_type = "ENTRY"
                if "EXIT" in s["history"]:
                    event_type = "REENTRY"
                
                s["history"].append(event_type)
                
                events.append(self._create_event(
                    visitor_id=visitor_id,
                    event_type=event_type,
                    timestamp=frame_time,
                    confidence=confidence,
                    is_staff=s["is_staff"],
                    metadata={"session_seq": len(s["history"])}
                ))

            # 3. ZONE TRANSITIONS (Skincare, Cosmetics, Billing)
            detected_zone = None
            if in_skincare[idx]:
                detected_zone = "SKINCARE"
            elif in_cosmetics[idx]:
                detected_zone = "COSMETICS"
            elif in_billing[idx]:
                detected_zone = "BILLING_QUEUE"
                
            previous_zone = s["current_zone"]
            
            if detected_zone != previous_zone:
                # Visitor exited their previous zone
                if previous_zone is not None:
                    dwell = int((frame_time - s["zone_enter_time"]).total_seconds() * 1000)
                    s["history"].append(f"{previous_zone}_EXIT")
                    
                    # Emit Zone Exit Event
                    events.append(self._create_event(
                        visitor_id=visitor_id,
                        event_type="ZONE_EXIT",
                        timestamp=frame_time,
                        zone_id=previous_zone,
                        dwell_ms=dwell,
                        confidence=confidence,
                        is_staff=s["is_staff"],
                        metadata={"sku_zone": "MOISTURISER" if previous_zone == "SKINCARE" else "LIPSTICK"}
                    ))
                    
                    # Emit Zone Dwell Event
                    events.append(self._create_event(
                        visitor_id=visitor_id,
                        event_type="ZONE_DWELL",
                        timestamp=frame_time,
                        zone_id=previous_zone,
                        dwell_ms=dwell,
                        confidence=confidence,
                        is_staff=s["is_staff"]
                    ))

                    # Queue Abandonment check: if left billing queue without a checkout purchase
                    if previous_zone == "BILLING_QUEUE" and s["in_queue"]:
                        s["in_queue"] = False
                        queue_dwell = int((frame_time - s["queue_join_time"]).total_seconds() * 1000)
                        
                        # We wait to see if they purchase. If they move back to shopping zones, it's an abandonment!
                        if detected_zone in ["SKINCARE", "COSMETICS"]:
                            s["history"].append("BILLING_QUEUE_ABANDON")
                            events.append(self._create_event(
                                visitor_id=visitor_id,
                                event_type="BILLING_QUEUE_ABANDON",
                                timestamp=frame_time,
                                zone_id="BILLING_QUEUE",
                                dwell_ms=queue_dwell,
                                confidence=confidence,
                                is_staff=s["is_staff"]
                            ))

                # Visitor enters the new zone
                if detected_zone is not None:
                    s["current_zone"] = detected_zone
                    s["zone_enter_time"] = frame_time
                    s["history"].append(f"{detected_zone}_ENTER")
                    
                    # Determine event type
                    event_type = "ZONE_ENTER"
                    if detected_zone == "BILLING_QUEUE":
                        event_type = "BILLING_QUEUE_JOIN"
                        s["in_queue"] = True
                        s["queue_join_time"] = frame_time

                    events.append(self._create_event(
                        visitor_id=visitor_id,
                        event_type=event_type,
                        timestamp=frame_time,
                        zone_id=detected_zone,
                        confidence=confidence,
                        is_staff=s["is_staff"]
                    ))
            
            # 4. EXIT / PURCHASE HANDLING (Line Crossing OUT)
            triggered_exit = crossed_out[idx]
            
            if triggered_exit and visitor_id not in self.emitted_exits:
                s["has_exited"] = True
                self.emitted_exits.add(visitor_id)
                s["history"].append("EXIT")
                
                # Check for conversion purchase:
                # If they were queued in the billing queue for at least 2.5 seconds before exit,
                # we flag this exit as a successful transaction/conversion!
                was_converted = False
                if s["queue_join_time"] is not None:
                    dwell_in_queue = (frame_time - s["queue_join_time"]).total_seconds()
                    if dwell_in_queue >= 2.5:
                        was_converted = True
                
                if was_converted:
                    # Emit Checkout Purchase Event
                    s["history"].append("PURCHASE")
                    events.append(self._create_event(
                        visitor_id=visitor_id,
                        event_type="PURCHASE",
                        timestamp=frame_time,
                        confidence=confidence,
                        is_staff=s["is_staff"]
                    ))
                    
                # Emit Exit Event
                events.append(self._create_event(
                    visitor_id=visitor_id,
                    event_type="EXIT",
                    timestamp=frame_time,
                    confidence=confidence,
                    is_staff=s["is_staff"]
                ))
                
        # 5. IMPLICIT TIMEOUT EXITS (For tracks that disappear near boundaries without line trigger)
        for visitor_id, s in self.sessions.items():
            if visitor_id not in active_track_ids and s["has_entered"] and not s["has_exited"]:
                time_since_seen = (frame_time - s["last_seen"]).total_seconds()
                if time_since_seen >= 4.0:  # Not seen for 4 seconds -> implicit store exit
                    s["has_exited"] = True
                    self.emitted_exits.add(visitor_id)
                    s["history"].append("EXIT")
                    
                    # Checkout conversion check on timeout exit
                    was_converted = False
                    if s["queue_join_time"] is not None:
                        dwell_in_queue = (s["last_seen"] - s["queue_join_time"]).total_seconds()
                        if dwell_in_queue >= 2.5:
                            was_converted = True
                            
                    if was_converted:
                        s["history"].append("PURCHASE")
                        events.append(self._create_event(
                            visitor_id=visitor_id,
                            event_type="PURCHASE",
                            timestamp=s["last_seen"],
                            confidence=0.90,
                            is_staff=s["is_staff"]
                        ))
                    
                    events.append(self._create_event(
                        visitor_id=visitor_id,
                        event_type="EXIT",
                        timestamp=s["last_seen"],
                        confidence=0.88,
                        is_staff=s["is_staff"]
                    ))
                    
        return events

    def _create_event(
        self,
        visitor_id: str,
        event_type: str,
        timestamp: datetime.datetime,
        confidence: float,
        is_staff: bool,
        zone_id: str = None,
        dwell_ms: int = None,
        metadata: dict = None
    ) -> Dict[str, Any]:
        return {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp.isoformat() + "Z",
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(confidence, 2),
            "metadata": metadata or {"queue_depth": None, "sku_zone": None, "session_seq": len(self.sessions[visitor_id]["history"])}
        }
