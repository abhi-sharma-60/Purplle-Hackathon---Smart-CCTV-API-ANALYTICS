import cv2
import supervision as sv
from ultralytics import YOLO

# Load YOLOv8 model
model = YOLO("yolov8s.pt")  # or yolov8n.pt for faster speed

VIDEO_PATH = "mall_counting.mp4"  #  human video file

# Get video size so line is always visible
cap = cv2.VideoCapture(VIDEO_PATH)
ret, frame = cap.read()
H, W = frame.shape[:2]
cap.release()

# Place line in the middle of the frame
line_zone = sv.LineZone(
    start=sv.Point(0, H // 2),
    end=sv.Point(W, H // 2)
)
line_annotator = sv.LineZoneAnnotator()

# Bounding box annotator
box_annotator = sv.BoxAnnotator(thickness=2)

# Tracking & counting loop
for result in model.track(source=VIDEO_PATH, stream=True, classes=[0], imgsz=640):
    frame = result.orig_img
    detections = sv.Detections.from_ultralytics(result)

    # Draw bounding boxes
    frame = box_annotator.annotate(scene=frame, detections=detections)

    # Count when objects cross the line
    line_zone.trigger(detections)
    line_annotator.annotate(frame, line_zone)

    # Display counts
    cv2.putText(frame, f"People (In): {line_zone.in_count}", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.putText(frame, f"People (Out): {line_zone.out_count}", (30, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

    cv2.imshow("People Tracking & Counting", frame)
    if cv2.waitKey(1) & 0xFF == 27:
        break

cv2.destroyAllWindows()
