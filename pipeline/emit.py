import logging
import requests
from typing import List, Dict, Any

logger = logging.getLogger("store_intelligence.pipeline.emitter")

# Default ingestion API endpoint
INGEST_URL = "http://localhost:8000/events/ingest"

class EventEmitter:
    def __init__(self, endpoint_url: str = INGEST_URL):
        self.endpoint_url = endpoint_url
        self.buffer: List[Dict[str, Any]] = []

    def queue_event(self, event: Dict[str, Any]):
        self.buffer.append(event)
        
        # If buffer reaches threshold (e.g. 50 events for quick processing, or 500 max), flush it!
        if len(self.buffer) >= 100:
            self.flush()

    def flush(self) -> bool:
        if not self.buffer:
            return True
            
        # Ensure we slice in batches of 500
        batch = self.buffer[:500]
        self.buffer = self.buffer[500:]

        try:
            logger.info(f"Attempting to transmit batch of {len(batch)} behavioral events to backend...")
            headers = {"Content-Type": "application/json"}
            
            response = requests.post(
                self.endpoint_url,
                json=batch,
                headers=headers,
                timeout=5.0
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(
                    f"Batch successfully ingested: processed={data.get('processed')}, "
                    f"skipped_duplicates={data.get('duplicates_skipped')}, "
                    f"failures={len(data.get('failures', []))}"
                )
                return True
            else:
                logger.error(
                    f"Backend rejected event ingestion batch. "
                    f"Status Code: {response.status_code}, Detail: {response.text}"
                )
                # Re-queue failed items to avoid losing telemetry data (graceful degradation)
                self.buffer = batch + self.buffer
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Failed to reach event ingestion endpoint {self.endpoint_url}. "
                f"Network issue or server down. Buffering events locally. Error: {str(e)}"
            )
            self.buffer = batch + self.buffer
            return False
            
    def force_flush_all(self):
        while self.buffer:
            success = self.flush()
            if not success:
                # If server is unreachable, stop blocking loop
                logger.warning(f"Forced flushing aborted due to connection issues. {len(self.buffer)} events stored in cache.")
                break
