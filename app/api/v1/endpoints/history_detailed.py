"""
History Detailed endpoints.
Full CAN Bus telemetry data with nested JSON structure.
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, Integer, BigInteger, bindparam
from sqlalchemy.dialects.postgresql import ARRAY
from io import StringIO
import csv

from app.core.database import get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.vehicle import Vehicle
from app.schemas.history import (
    HistoryDetailedCursorResponse,
    CompositeCursor,
    UnitInfo,
    DriverInfo,
    GroupInfo,
    SubgroupInfo,
    PoiInfo,
    CercaInfo,
    DeviceInfo,
    EventInfo,
    ElectricalData,
    LocationData,
    EngineData,
    SpeedDistanceData,
    FuelData,
    TemperatureData,
    TransmissionData,
    PneumaticData,
    HourmeterData,
    StatusFlagsData
)

router = APIRouter()


def validate_date_range(start_date: datetime, end_date: datetime, max_days: int = 31) -> None:
    """Validate date range is within maximum days."""
    if end_date <= start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be after start_date"
        )

    delta = end_date - start_date
    if delta.days > max_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date range cannot exceed {max_days} days. Current range: {delta.days} days"
        )


def build_history_detailed_query() -> str:
    """Build the SQL query for detailed history with all CAN Bus data."""
    return """
        SELECT
            ds.id,
            -- Unit info
            tu.id AS unit_id,
            tu.label AS unit_label,
            tu.label2 AS unit_label2,
            tu.obs AS unit_obs,
            tu.group_id AS unit_group_id,
            gu.name AS unit_group_name,
            tu.subgroup_id AS unit_subgroup_id,
            sgu.name AS unit_subgroup_name,
            -- Driver info
            ds.driver_id,
            ds.driver_name,
            ds.driver_login,
            d.group_id AS driver_group_id,
            gd.name AS driver_group_name,
            d.subgroup_id AS driver_subgroup_id,
            sgd.name AS driver_subgroup_name,
            -- Basic fields
            to_char(ds.local_time, 'YYYY-MM-DD HH24:MI:SS') AS local_time,
            to_char(ds.time_write, 'YYYY-MM-DD HH24:MI:SS') AS time_write,
            ds.latitude,
            ds.longitude,
            ds.ignition AS ign,
            ds.speed,
            COALESCE(ds.odom_total, ds.odom) AS odom,
            ds.rpm,
            ds.address,
            -- POI
            ds.poi_id,
            ds.poi_name,
            ds.poi_distance,
            -- Cerca/Area
            ds.area_id,
            ds.area_name,
            -- Device
            dv.id AS device_id,
            dv.identifier AS device_identifier,
            dm.name AS device_model,
            -- Event
            te.id AS event_id,
            te.name AS event_name,
            -- Electrical data
            ds.voltage,
            ds.battery,
            ds.can_control_module_voltage,
            -- Location details
            ds.altitude,
            ds.direction,
            ds.gps,
            -- Engine CAN
            ds.can_rpm,
            ds.can_accel_pedal_percent,
            ds.can_engine_torque_percent,
            ds.can_retarder_torque,
            ds.can_engine_oil_pressure,
            ds.can_turbo_charger_pressure,
            -- Speed/Distance CAN
            ds.can_speed,
            ds.can_total_odometer,
            -- Fuel CAN
            ds.can_fuel_level_percent,
            ds.can_def_level_percent,
            ds.can_total_used_fuel,
            -- Temperature CAN
            ds.can_engine_coolant_temp,
            ds.can_engine_coolant_level,
            ds.external_sensor_temperature,
            -- Transmission CAN
            ds.can_gear,
            ds.faixa,
            -- Pneumatic CAN
            ds.can_pneumatic_system1_pressure,
            ds.can_pneumatic_system2_pressure,
            -- Hourmeter
            ds.hourmeter,
            ds.can_engine_hourmeter,
            -- Status flags
            ds.in5,
            ds.in6,
            ds.in7,
            ds.in8,
            ds.can_cruise_control_state,
            ds.can_break_pedal_state,
            ds.can_parking_brake_state,
            ds.can_retarder_in_use,
            ds.connection
        FROM mova.dev_status_30 ds
        INNER JOIN mova.tracked_unit tu ON ds.unit_id = tu.id
        INNER JOIN mova."group" gu ON tu.group_id = gu.id
        INNER JOIN mova.subgroup sgu ON tu.subgroup_id = sgu.id
        INNER JOIN mova.device dv ON ds.device_id = dv.id
        INNER JOIN mova.device_model dm ON dv.device_model_id = dm.id
        INNER JOIN mova.tracker_event te ON ds.tracker_event_id = te.id
        LEFT JOIN mova.driver d ON d.id = ds.driver_id
        LEFT JOIN mova."group" gd ON gd.id = d.group_id
        LEFT JOIN mova.subgroup sgd ON sgd.id = d.subgroup_id
    """


def row_to_detailed_response(row) -> dict:
    """Convert a database row to HistoryDetailedResponse dict."""
    # Build unit info
    unit = UnitInfo(
        id=row.unit_id,
        label=row.unit_label,
        label2=row.unit_label2,
        obs=row.unit_obs,
        group=GroupInfo(
            id=row.unit_group_id,
            name=row.unit_group_name,
            subgroup=SubgroupInfo(
                id=row.unit_subgroup_id,
                name=row.unit_subgroup_name
            )
        )
    )

    # Build driver info (can be null)
    driver = None
    if row.driver_id:
        driver = DriverInfo(
            id=row.driver_id,
            name=row.driver_name,
            login=row.driver_login,
            group=GroupInfo(
                id=row.driver_group_id,
                name=row.driver_group_name,
                subgroup=SubgroupInfo(
                    id=row.driver_subgroup_id,
                    name=row.driver_subgroup_name
                )
            ) if row.driver_group_id else None
        )

    return {
        "id": row.id,
        "unit": unit,
        "driver": driver,
        "local_time": row.local_time,
        "time_write": row.time_write,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "ign": row.ign,
        "speed": row.speed,
        "odom": row.odom,
        "rpm": row.rpm,
        "address": row.address,
        "poi": PoiInfo(
            id=row.poi_id,
            name=row.poi_name,
            distance=row.poi_distance
        ),
        "cerca": CercaInfo(
            id=row.area_id,
            name=row.area_name
        ),
        "device": DeviceInfo(
            id=row.device_id,
            identifier=row.device_identifier,
            model=row.device_model
        ),
        "event": EventInfo(
            id=row.event_id,
            name=row.event_name
        ),
        "electrical_data": ElectricalData(
            voltage=row.voltage,
            battery=row.battery,
            can_control_module_voltage=row.can_control_module_voltage
        ),
        "location": LocationData(
            latitude=row.latitude,
            longitude=row.longitude,
            altitude=row.altitude,
            direction=row.direction,
            gps=row.gps
        ),
        "engine": EngineData(
            rpm=row.rpm,
            can_rpm=row.can_rpm,
            can_accel_pedal_percent=row.can_accel_pedal_percent,
            can_engine_torque_percent=row.can_engine_torque_percent,
            can_retarder_torque=row.can_retarder_torque,
            can_engine_oil_pressure=row.can_engine_oil_pressure,
            can_turbo_charger_pressure=row.can_turbo_charger_pressure
        ),
        "speed_distance": SpeedDistanceData(
            speed=row.speed,
            can_speed=row.can_speed,
            odom=row.odom,
            can_total_odometer=row.can_total_odometer
        ),
        "fuel": FuelData(
            can_fuel_level_percent=row.can_fuel_level_percent,
            can_def_level_percent=row.can_def_level_percent,
            can_total_used_fuel=row.can_total_used_fuel
        ),
        "temperature": TemperatureData(
            can_engine_coolant_temp=row.can_engine_coolant_temp,
            can_engine_coolant_level=row.can_engine_coolant_level,
            external_sensor_temperature=row.external_sensor_temperature
        ),
        "transmission": TransmissionData(
            can_gear=row.can_gear,
            faixa=row.faixa
        ),
        "pneumatic": PneumaticData(
            can_pneumatic_system1_pressure=row.can_pneumatic_system1_pressure,
            can_pneumatic_system2_pressure=row.can_pneumatic_system2_pressure
        ),
        "hourmeter": HourmeterData(
            hourmeter=row.hourmeter,
            can_engine_hourmeter=row.can_engine_hourmeter
        ),
        "status_flags": StatusFlagsData(
            ignition=row.ign,
            in5=row.in5,
            in6=row.in6,
            in7=row.in7,
            in8=row.in8,
            can_cruise_control_state=row.can_cruise_control_state,
            can_break_pedal_state=row.can_break_pedal_state,
            can_parking_brake_state=row.can_parking_brake_state,
            can_retarder_in_use=row.can_retarder_in_use,
            connection=row.connection
        )
    }


@router.get("/history/detailed/cursor", response_model=HistoryDetailedCursorResponse, tags=["Reports/History"])
async def get_history_detailed_cursor(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    cursor: Optional[str] = Query(None, description="Pagination cursor from previous response"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    List detailed History records with full CAN Bus data using cursor pagination.

    **Features:**
    - Complete CAN Bus telemetry data
    - Nested JSON structure (unit, driver, device, event, etc.)
    - Partition-optimized cursor pagination
    - Auto-filtered by group/subgroup access

    **Response Structure:**
    - unit: Vehicle info with group/subgroup
    - driver: Driver info with group/subgroup (null if no driver)
    - poi: Point of Interest
    - cerca: Geofence/Area
    - device: Tracking device info
    - event: Tracker event
    - electrical_data, location, engine, speed_distance, fuel, temperature, transmission, pneumatic, hourmeter, status_flags
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Validate date range
    validate_date_range(start_date, end_date)

    # Check user has group access
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get accessible vehicle IDs
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    vehicle_query = select(Vehicle.id).where(access_filter, Vehicle.status == 1)

    if vehicle_ids:
        requested_ids = [int(vid.strip()) for vid in vehicle_ids.split(",")]
        vehicle_query = vehicle_query.where(Vehicle.id.in_(requested_ids))

    vehicle_result = await db.execute(vehicle_query)
    accessible_vehicle_ids = [row[0] for row in vehicle_result.fetchall()]

    if not accessible_vehicle_ids:
        return HistoryDetailedCursorResponse(
            data=[],
            next_cursor=None,
            has_more=False,
            total_returned=0
        )

    # Parse cursor if provided
    cursor_time = None
    cursor_id = None
    if cursor:
        try:
            parsed_cursor = CompositeCursor.from_string(cursor)
            # Convert string to datetime
            cursor_time = datetime.strptime(parsed_cursor.local_time, "%Y-%m-%d %H:%M:%S")
            cursor_id = parsed_cursor.id
        except Exception as e:
            logger.warning(f"Invalid cursor format: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format"
            )

    # Build query
    base_query = build_history_detailed_query()

    # Build cursor condition based on whether cursor exists
    if cursor_time and cursor_id:
        cursor_condition = """
            AND (
                ds.local_time < :cursor_time
                OR (ds.local_time = :cursor_time AND ds.id < :cursor_id)
            )
        """
        query = text(f"""
            {base_query}
            WHERE ds.local_time >= :start_date
                AND ds.local_time < :end_date
                AND tu.id = ANY(:vehicle_ids)
                {cursor_condition}
            ORDER BY ds.local_time DESC, ds.id DESC
            LIMIT :limit
        """).bindparams(
            bindparam("vehicle_ids", type_=ARRAY(Integer)),
            bindparam("cursor_id", type_=BigInteger)
        )

        params = {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_ids": accessible_vehicle_ids,
            "cursor_time": cursor_time,
            "cursor_id": cursor_id,
            "limit": limit + 1
        }
    else:
        query = text(f"""
            {base_query}
            WHERE ds.local_time >= :start_date
                AND ds.local_time < :end_date
                AND tu.id = ANY(:vehicle_ids)
            ORDER BY ds.local_time DESC, ds.id DESC
            LIMIT :limit
        """).bindparams(
            bindparam("vehicle_ids", type_=ARRAY(Integer))
        )

        params = {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_ids": accessible_vehicle_ids,
            "limit": limit + 1
        }

    result = await db.execute(query, params)

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Convert rows to response objects
    data = [row_to_detailed_response(row) for row in rows]

    # Build next cursor
    next_cursor = None
    if has_more and rows:
        last_row = rows[-1]
        next_cursor = CompositeCursor(
            local_time=last_row.local_time,
            id=last_row.id
        ).to_string()

    logger.info(
        "history_detailed_cursor_query",
        user_id=current_user.user_id,
        vehicle_count=len(accessible_vehicle_ids),
        returned_rows=len(data),
        has_more=has_more
    )

    return HistoryDetailedCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.post("/history/detailed/export/estimate", tags=["Reports/History"])
async def estimate_detailed_export_size(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate export size for detailed history data.
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    validate_date_range(start_date, end_date)

    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get accessible vehicle IDs
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    vehicle_query = select(Vehicle.id).where(access_filter, Vehicle.status == 1)

    if vehicle_ids:
        requested_ids = [int(vid.strip()) for vid in vehicle_ids.split(",")]
        vehicle_query = vehicle_query.where(Vehicle.id.in_(requested_ids))

    vehicle_result = await db.execute(vehicle_query)
    accessible_vehicle_ids = [row[0] for row in vehicle_result.fetchall()]

    if not accessible_vehicle_ids:
        return {
            "estimated_rows": 0,
            "estimated_size_mb": 0,
            "estimated_time_seconds": 0,
            "date_range_days": (end_date - start_date).days
        }

    # Count records
    count_query = text("""
        SELECT COUNT(*) as total
        FROM mova.dev_status_30 ds
        WHERE ds.local_time >= :start_date
            AND ds.local_time < :end_date
            AND ds.unit_id = ANY(:vehicle_ids)
    """).bindparams(
        bindparam("vehicle_ids", type_=ARRAY(Integer))
    )

    result = await db.execute(
        count_query,
        {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_ids": accessible_vehicle_ids
        }
    )

    row_count = result.scalar()

    # Estimate size (larger due to full CAN data - ~500 bytes per JSON record)
    estimated_size_mb = (row_count * 500) / 1024 / 1024

    # Estimate time
    estimated_time_seconds = max(row_count / 50000, 1)  # Slower due to JOINs

    logger.info(
        "history_detailed_export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1)
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1),
        "date_range_days": (end_date - start_date).days
    }


@router.get("/history/detailed/export/csv", tags=["Reports/History"])
async def export_history_detailed_csv(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export detailed History records as CSV with parallel partition processing.

    **Features:**
    - Full CAN Bus telemetry data in CSV format
    - Parallel partition processing for speed
    - Streaming response (memory efficient)
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    validate_date_range(start_date, end_date)

    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Get accessible vehicle IDs
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    vehicle_query = select(Vehicle.id).where(access_filter, Vehicle.status == 1)

    if vehicle_ids:
        requested_ids = [int(vid.strip()) for vid in vehicle_ids.split(",")]
        vehicle_query = vehicle_query.where(Vehicle.id.in_(requested_ids))

    vehicle_result = await db.execute(vehicle_query)
    accessible_vehicle_ids = [row[0] for row in vehicle_result.fetchall()]

    if not accessible_vehicle_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No accessible vehicles found"
        )

    def get_daily_partitions(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Split date range into daily partitions."""
        partitions = []
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date_adj = end.replace(hour=23, minute=59, second=59, microsecond=999999)

        while current <= end_date_adj:
            partition_start = max(current, start)
            partition_end = min(current + timedelta(days=1) - timedelta(microseconds=1), end)

            if partition_start <= partition_end:
                partitions.append((partition_start, partition_end))

            current += timedelta(days=1)

        return partitions

    async def query_partition(
        partition_start: datetime,
        partition_end: datetime,
        vehicle_ids_list: list[int]
    ) -> list:
        """Query data for a single daily partition."""
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            base_query = build_history_detailed_query()
            query = text(f"""
                {base_query}
                WHERE ds.local_time >= :partition_start
                    AND ds.local_time <= :partition_end
                    AND tu.id = ANY(:vehicle_ids)
                ORDER BY ds.local_time DESC, ds.id DESC
            """).bindparams(
                bindparam("vehicle_ids", type_=ARRAY(Integer))
            )

            result = await session.execute(
                query,
                {
                    "partition_start": partition_start,
                    "partition_end": partition_end,
                    "vehicle_ids": vehicle_ids_list
                }
            )

            return result.fetchall()

    async def generate_csv_parallel():
        """Stream CSV with parallel partition processing."""
        import asyncio

        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Local Time", "Time Write",
            "Unit ID", "Unit Label", "Unit Label2", "Unit Obs", "Unit Group ID", "Unit Group Name", "Unit Subgroup ID", "Unit Subgroup Name",
            "Driver ID", "Driver Name", "Driver Login", "Driver Group ID", "Driver Group Name", "Driver Subgroup ID", "Driver Subgroup Name",
            "Latitude", "Longitude", "Ignition", "Speed", "Odometer", "RPM", "Address",
            "POI ID", "POI Name", "POI Distance",
            "Area ID", "Area Name",
            "Device ID", "Device Identifier", "Device Model",
            "Event ID", "Event Name",
            "Voltage", "Battery", "CAN Control Module Voltage",
            "Altitude", "Direction", "GPS",
            "CAN RPM", "CAN Accel Pedal %", "CAN Engine Torque %", "CAN Retarder Torque", "CAN Engine Oil Pressure", "CAN Turbo Charger Pressure",
            "CAN Speed", "CAN Total Odometer",
            "CAN Fuel Level %", "CAN DEF Level %", "CAN Total Used Fuel",
            "CAN Engine Coolant Temp", "CAN Engine Coolant Level", "External Sensor Temp",
            "CAN Gear", "Faixa",
            "CAN Pneumatic System1 Pressure", "CAN Pneumatic System2 Pressure",
            "Hourmeter", "CAN Engine Hourmeter",
            "IN5", "IN6", "IN7", "IN8",
            "CAN Cruise Control State", "CAN Brake Pedal State", "CAN Parking Brake State", "CAN Retarder In Use", "Connection"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        daily_partitions = get_daily_partitions(start_date, end_date)
        total_partitions = len(daily_partitions)

        logger.info(
            "history_detailed_csv_export_started",
            total_partitions=total_partitions,
            vehicle_count=len(accessible_vehicle_ids)
        )

        MAX_PARALLEL = 10
        total_rows = 0

        for batch_start in range(0, total_partitions, MAX_PARALLEL):
            batch_end = min(batch_start + MAX_PARALLEL, total_partitions)
            batch_partitions = daily_partitions[batch_start:batch_end]

            tasks = [
                query_partition(p_start, p_end, accessible_vehicle_ids)
                for p_start, p_end in batch_partitions
            ]

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error(f"Partition query failed: {result}")
                    continue

                for row in result:
                    writer.writerow([
                        row.id, row.local_time, row.time_write,
                        row.unit_id, row.unit_label, row.unit_label2, row.unit_obs, row.unit_group_id, row.unit_group_name, row.unit_subgroup_id, row.unit_subgroup_name,
                        row.driver_id, row.driver_name, row.driver_login, row.driver_group_id, row.driver_group_name, row.driver_subgroup_id, row.driver_subgroup_name,
                        row.latitude, row.longitude, row.ign, row.speed, row.odom, row.rpm, row.address,
                        row.poi_id, row.poi_name, row.poi_distance,
                        row.area_id, row.area_name,
                        row.device_id, row.device_identifier, row.device_model,
                        row.event_id, row.event_name,
                        row.voltage, row.battery, row.can_control_module_voltage,
                        row.altitude, row.direction, row.gps,
                        row.can_rpm, row.can_accel_pedal_percent, row.can_engine_torque_percent, row.can_retarder_torque, row.can_engine_oil_pressure, row.can_turbo_charger_pressure,
                        row.can_speed, row.can_total_odometer,
                        row.can_fuel_level_percent, row.can_def_level_percent, row.can_total_used_fuel,
                        row.can_engine_coolant_temp, row.can_engine_coolant_level, row.external_sensor_temperature,
                        row.can_gear, row.faixa,
                        row.can_pneumatic_system1_pressure, row.can_pneumatic_system2_pressure,
                        row.hourmeter, row.can_engine_hourmeter,
                        row.in5, row.in6, row.in7, row.in8,
                        row.can_cruise_control_state, row.can_break_pedal_state, row.can_parking_brake_state, row.can_retarder_in_use, row.connection
                    ])
                    total_rows += 1

                yield output.getvalue()
                output.truncate(0)
                output.seek(0)

        logger.info(
            "history_detailed_csv_export_completed",
            total_rows=total_rows
        )

    filename = f"history_detailed_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv_parallel(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )
