"""
Telemetry model - Consolidated trip data from mova.con_telemetry.

This table stores consolidated trip telemetry data calculated from ignition ON/OFF events.
Partitioned monthly (con_telemetry_p202501, con_telemetry_p202502, etc.).

Key metrics tracked:
- Trip details (start/end time, location, POI, area)
- Distance and time (total, moving, stopped, etc.)
- Speed metrics (max, avg, violations)
- RPM metrics (time in ranges, violations)
- Fuel consumption and efficiency
- Driver behavior (hard accel/brake, clutch excess, etc.)
- Advanced features (eco-roll, retarder, autopilot distances)
"""

from sqlalchemy import Column, Integer, BigInteger, String, Numeric, Boolean, DateTime, func
from app.core.database import Base


class Telemetry(Base):
    """
    Consolidated trip telemetry data.

    Partitioned by month: con_telemetry_pYYYYMM
    Access control: via unit_id (vehicle)
    """
    __tablename__ = "con_telemetry"
    __table_args__ = {'schema': 'mova'}

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Trip Identification
    trip_id = Column(BigInteger, nullable=False, index=True)
    trip_number = Column(Integer)
    trip_status = Column(Boolean)
    trip_direction = Column(Integer)
    trip_opening_date = Column(DateTime)

    # Vehicle & Device
    unit_id = Column(Integer, nullable=False, index=True)  # FK to tracked_unit (for access control)
    unit_label = Column(String, nullable=False)
    device_id = Column(Integer, nullable=False)

    # Driver
    driver_id = Column(Integer, index=True)
    driver_name = Column(String)

    # Odometer & Hourmeter
    start_odometer = Column(BigInteger)
    end_odometer = Column(BigInteger)
    distance_traveled = Column(Numeric)  # km
    start_hourmeter = Column(BigInteger)
    end_hourmeter = Column(BigInteger)

    # Time Metrics (seconds)
    total_time = Column(Integer)
    time_stopped = Column(Integer)
    time_moving = Column(Integer)
    time_raining = Column(Integer)
    time_dry = Column(Integer)
    time_engine_off = Column(Integer)

    # Trip Start Details
    start_time = Column(DateTime, nullable=False, index=True)
    start_lat = Column(Numeric(9, 6))
    start_lon = Column(Numeric(9, 6))
    start_poi_id = Column(Integer)
    start_poi_name = Column(String)
    start_poi_distance = Column(Integer)
    start_area_id = Column(Integer)
    start_area_name = Column(String)

    # Trip End Details
    end_time = Column(DateTime, index=True)
    end_lat = Column(Numeric(9, 6))
    end_lon = Column(Numeric(9, 6))
    end_poi_id = Column(Integer)
    end_poi_name = Column(String)
    end_poi_distance = Column(Integer)
    end_area_id = Column(Integer)
    end_area_name = Column(String)

    # Speed Metrics
    max_speed = Column(Integer)
    avg_speed = Column(Integer)
    count_over_speed = Column(Integer)
    time_over_speed = Column(Integer)

    # Speed Violations - Urban
    max_urban_permitted_speed = Column(Integer)
    urban_rain_speed = Column(Integer)
    time_over_urban_speed = Column(Integer)
    time_over_urban_rain_speed = Column(Integer)
    reached_over_urban_speed = Column(Integer)
    reached_over_urban_rain_speed = Column(Integer)
    count_over_urban_speed = Column(Integer)
    count_over_urban_rain_speed = Column(Integer)

    # Speed Violations - Road
    max_road_permitted_speed = Column(Integer)
    max_road_rain_speed = Column(Integer)
    time_over_road_speed = Column(Integer)
    time_over_road_rain_speed = Column(Integer)
    reached_over_road_speed = Column(Integer)
    reached_over_road_rain_speed = Column(Integer)
    count_over_road_speed = Column(Integer)
    count_over_road_rain_speed = Column(Integer)

    # RPM Metrics
    max_rpm_permitted = Column(Integer)
    time_over_rpm = Column(Integer)
    reached_rpm = Column(Integer)

    # RPM Ranges (blue, green, extra-eco, yellow, red)
    time_blue = Column(Integer)
    count_blue = Column(Integer)
    time_green = Column(Integer)
    count_green = Column(Integer)
    time_extra_eco = Column(Integer)
    count_extra_eco = Column(Integer)
    time_yellow = Column(Integer)
    count_yellow = Column(Integer)
    time_red = Column(Integer)
    count_red = Column(Integer)

    # Engine & Stop Metrics
    time_stop_engine_on = Column(Integer)
    count_stop_engine_on = Column(Integer)
    time_stop_engine_on_productive = Column(Integer)
    time_stop_accel = Column(Integer)
    count_stop_accel = Column(Integer)

    # Gear & Clutch
    time_banguela = Column(Integer)  # Neutral gear
    count_banguela = Column(Integer)
    count_clutch = Column(Integer)
    time_cluth_excess = Column(Integer)
    count_cluth_excess = Column(Integer)

    # Fuel
    start_fuel = Column(Numeric)
    end_fuel = Column(Numeric)
    fuel_used = Column(Numeric)
    fuel_used_stopped = Column(Numeric)
    efficiency_kml = Column(Numeric)  # km/liter

    # Driver Behavior
    count_hard_acel = Column(Integer)
    count_hard_brake = Column(Integer)
    count_harsh_turn = Column(Integer)
    count_speed_violation_l2 = Column(Integer)
    count_speed_violation_l3 = Column(Integer)

    # Advanced Features
    time_tolerancia = Column(Integer)
    time_inercia = Column(Integer)
    time_low_speed = Column(Integer)
    time_eco_roll = Column(Integer)
    time_retarder = Column(Integer)
    time_autopilot = Column(Integer)
    time_over_turbo_pressure = Column(Integer)
    time_under_turbo_pressure = Column(Integer)

    # Engine Load Levels
    time_engine_load_level1 = Column(Integer)
    time_engine_load_level2 = Column(Integer)
    time_engine_load_level3 = Column(Integer)

    # Distance Metrics (advanced)
    distance_pulling = Column(Integer)
    distance_simple_inertia = Column(Integer)
    distance_retarder = Column(Integer)
    distance_ecoroll = Column(Integer)
    distance_autopilot = Column(Integer)

    # Journey & Line
    journey_status = Column(Boolean)
    journey_opening_date = Column(DateTime)
    line = Column(String)
    line_number = Column(Integer)

    # System Fields
    calculated = Column(Integer, default=0)
    time_write = Column(DateTime, server_default=func.now())

    # Note: No relationship defined - table uses raw SQL JOINs for access control
    # Partitioned table (monthly) with logical relationship via unit_id -> tracked_unit.id

    def __repr__(self):
        return f"<Telemetry(id={self.id}, trip_id={self.trip_id}, unit_id={self.unit_id}, start_time={self.start_time})>"
