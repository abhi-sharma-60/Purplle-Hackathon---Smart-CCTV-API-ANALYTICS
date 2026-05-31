import numpy as np
import supervision as sv

class StoreZoneManager:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        
        # 1. Scale polygons dynamically based on frame width and height
        # Skincare Zone (left side layout)
        self.skincare_poly = np.array([
            [int(width * 0.05), int(height * 0.15)],
            [int(width * 0.40), int(height * 0.15)],
            [int(width * 0.40), int(height * 0.65)],
            [int(width * 0.05), int(height * 0.65)]
        ])
        
        # Cosmetics Zone (right side layout)
        self.cosmetics_poly = np.array([
            [int(width * 0.60), int(height * 0.15)],
            [int(width * 0.95), int(height * 0.15)],
            [int(width * 0.95), int(height * 0.65)],
            [int(width * 0.60), int(height * 0.65)]
        ])
        
        # Billing Queue Zone (bottom-center layout)
        self.billing_queue_poly = np.array([
            [int(width * 0.25), int(height * 0.68)],
            [int(width * 0.75), int(height * 0.68)],
            [int(width * 0.75), int(height * 0.95)],
            [int(width * 0.25), int(height * 0.95)]
        ])
        
        # Staff Counter Zone (deep bottom layout, employee-only area behind cashier)
        self.staff_zone_poly = np.array([
            [int(width * 0.40), int(height * 0.82)],
            [int(width * 0.60), int(height * 0.82)],
            [int(width * 0.60), int(height * 0.98)],
            [int(width * 0.40), int(height * 0.98)]
        ])

        # 2. Instantiate supervision polygon zones
        self.skincare_zone = sv.PolygonZone(polygon=self.skincare_poly)
        self.cosmetics_zone = sv.PolygonZone(polygon=self.cosmetics_poly)
        self.billing_queue_zone = sv.PolygonZone(polygon=self.billing_queue_poly)
        self.staff_zone = sv.PolygonZone(polygon=self.staff_zone_poly)

        # 3. Entry & Exit line crossing (horizontal division in the middle)
        self.line_zone = sv.LineZone(
            start=sv.Point(0, int(height * 0.50)),
            end=sv.Point(width, int(height * 0.50))
        )
        
        # 4. Annotators and styling colors
        self.blue_color = sv.Color.from_hex("#3B82F6")
        self.amber_color = sv.Color.from_hex("#F59E0B")
        self.line_annotator = sv.LineZoneAnnotator(
            thickness=2,
            color=sv.Color.from_hex("#10B981")  # Premium Green
        )

    def draw_zones(self, scene: np.ndarray) -> np.ndarray:
        # Draw outlines for zones
        scene = sv.draw_polygon(scene=scene, polygon=self.skincare_poly, color=self.blue_color, thickness=2)
        scene = sv.draw_polygon(scene=scene, polygon=self.cosmetics_poly, color=self.blue_color, thickness=2)
        scene = sv.draw_polygon(scene=scene, polygon=self.billing_queue_poly, color=self.amber_color, thickness=2)
        
        # Draw entry/exit line
        scene = self.line_annotator.annotate(frame=scene, line_counter=self.line_zone)
        
        return scene
