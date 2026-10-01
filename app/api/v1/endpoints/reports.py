"""
Reports endpoints.
Heavy query endpoints for History data with cursor pagination, streaming, and background jobs.
"""

from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, and_, or_, Integer, BigInteger, String, Date, bindparam
from sqlalchemy.dialects.postgresql import ARRAY
from io import StringIO
import csv

from app.core.database import get_db, get_db_read
from app.core.access_control import build_group_subgroup_filter
from app.core.query_filters import validate_and_build_access_params, SQLAccessControlBuilder
from app.api.v1.endpoints.reports_secure import get_secure_driver_report_params
from app.middleware.auth import AuthenticatedUser, require_permission
from app.models.history import History
from app.models.vehicle import Vehicle
from app.models.telemetry import Telemetry
from app.schemas.history import (
    HistoryResponse,
    HistoryCursorResponse,
    CompositeCursor
)
from app.schemas.driver_reports import (
    DriverKmFuelHoursResponse,
    DriverKmFuelHoursCursorResponse,
    DriverReportCompositeCursor,
    RpmBandTimeResponse,
    RpmBandTimeCursorResponse,
    HeatmapResponse,
    HeatmapCursorResponse,
    WeightRangeResponse,
    WeightRangeCursorResponse,
    WeightRangeCursor
)
from app.schemas.telemetry import (
    TelemetryResponse,
    TelemetryCursorResponse,
    TelemetryCompositeCursor
)

router = APIRouter()


def validate_date_range(start_date: datetime, end_date: datetime, max_days: int = 31) -> None:
    """
    Validate date range is within specified maximum days.

    Args:
        start_date: Start datetime
        end_date: End datetime
        max_days: Maximum allowed days (default: 31 for exports, use 93 for cursor endpoints)

    Raises:
        HTTPException: If date range exceeds max_days
    """
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


# Constants for date range validation
MAX_DAYS_EXPORT = 31   # Maximum days for CSV export endpoints
MAX_DAYS_CURSOR = 93   # Maximum days for cursor pagination endpoints (3 months)


@router.post("/history/export/estimate", tags=["Reports/History"])
async def estimate_export_size(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate export size and time before starting the actual export.

    **Returns:**
    - estimated_rows: Approximate number of rows
    - estimated_size_mb: Approximate file size in MB
    - estimated_time_seconds: Estimated time in seconds
    - recommended_method: 'streaming' or 'background'

    **Use Case:**
    - Frontend can show estimation to user before export
    - Choose appropriate export method based on size
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
    vehicle_query = select(Vehicle.id).where(access_filter)

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
            "recommended_method": "none"
        }

    # Fast count using PostgreSQL COUNT
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

    # Estimate file size (avg 150 bytes per CSV row)
    estimated_size_mb = (row_count * 150) / 1024 / 1024

    # Estimate time based on parallel performance
    # ~70k rows per partition per second (parallel)
    estimated_time_seconds = max(row_count / 70000, 2)

    # Recommend method
    if estimated_time_seconds < 60:
        recommended_method = "streaming"  # < 1 min
    elif estimated_time_seconds < 300:
        recommended_method = "streaming"  # < 5 min
    else:
        recommended_method = "background"  # > 5 min

    logger.info(
        "export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1),
        recommended_method=recommended_method
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1),
        "recommended_method": recommended_method,
        "date_range_days": (end_date - start_date).days + 1,
        "vehicle_count": len(accessible_vehicle_ids)
    }


@router.get("/history/cursor", response_model=HistoryCursorResponse, tags=["Reports/History"])
async def list_history_cursor(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    cursor: Optional[str] = Query(None, description="Composite cursor for partition-optimized pagination (base64-encoded)"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    List History records with partition-optimized cursor pagination (optimized for partitioned tables).

    **Partition Optimization:**
    - Uses composite cursor (local_time, id) for automatic partition pruning
    - PostgreSQL skips irrelevant daily partitions (dev_status_30_pYYYYMMDD)
    - 5-10x faster for large date ranges (30+ days)

    **Cursor Pagination Benefits:**
    - Efficient for large datasets (no OFFSET overhead)
    - Consistent results even with concurrent inserts
    - Fast performance regardless of page depth
    - Each page gets FASTER (vs traditional cursor that gets slower)

    **Usage:**
    1. First request: omit cursor parameter
    2. Subsequent requests: use `next_cursor` from previous response (base64-encoded)
    3. Continue until `has_more` is false

    **Access Control:**
    - Only returns data from vehicles user has access to (group/subgroup)

    **Date Range:**
    - Maximum 93 days (3 months) between start_date and end_date
    """
    # Validate date range (max 93 days for cursor endpoints)
    validate_date_range(start_date, end_date, max_days=MAX_DAYS_CURSOR)

    # Check user has group access
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter for vehicles
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Get accessible vehicle IDs
    vehicle_query = select(Vehicle.id).where(access_filter)

    # Filter by specific vehicle IDs if provided
    if vehicle_ids:
        requested_ids = [int(vid.strip()) for vid in vehicle_ids.split(",")]
        vehicle_query = vehicle_query.where(Vehicle.id.in_(requested_ids))

    vehicle_result = await db.execute(vehicle_query)
    accessible_vehicle_ids = [row[0] for row in vehicle_result.fetchall()]

    if not accessible_vehicle_ids:
        return HistoryCursorResponse(
            data=[],
            next_cursor=None,
            has_more=False,
            total_returned=0
        )

    # Decode composite cursor if provided (with backward compatibility)
    cursor_time = None
    cursor_id = None
    if cursor:
        try:
            # Try new composite cursor format (base64)
            decoded_cursor = CompositeCursor.from_string(cursor)
            cursor_time = decoded_cursor.local_time
            cursor_id = decoded_cursor.id
        except Exception:
            # Fallback: try old cursor format (plain integer ID)
            # This provides backward compatibility with old cursors
            try:
                cursor_id = int(cursor)
                cursor_time = None  # Old format doesn't have time-based pruning
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cursor format. Expected base64-encoded composite cursor or integer ID."
                )

    # Build query with partition-optimized composite cursor
    # Using raw SQL for optimal performance with complex JOIN and formatting
    # Note: cursor_time is a string in 'YYYY-MM-DD HH24:MI:SS' format
    query = text("""
        SELECT
            ds.id,
            tu.label,
            tu.label2,
            tu.obs,
            to_char(ds.local_time, 'YYYY-MM-DD HH24:MI:SS') AS local_time,
            to_char(ds.time_write, 'YYYY-MM-DD HH24:MI:SS') AS time_write,
            ds.latitude,
            ds.longitude,
            ds.ignition AS ign,
            ds.speed,
            COALESCE(ds.odom_total, ds.odom) AS odom,
            ds.rpm,
            ds.address
        FROM mova.dev_status_30 ds
        INNER JOIN mova.tracked_unit tu ON ds.unit_id = tu.id
        WHERE ds.local_time >= :start_date
            AND ds.local_time < :end_date
            AND tu.id = ANY(:vehicle_ids)
            AND (
                :cursor_time IS NULL
                OR ds.local_time < CAST(:cursor_time AS timestamp)
                OR (ds.local_time = CAST(:cursor_time AS timestamp) AND ds.id < :cursor_id)
            )
        ORDER BY ds.local_time DESC, ds.id DESC
        LIMIT :limit
    """).bindparams(
        bindparam("vehicle_ids", type_=ARRAY(Integer)),
        bindparam("cursor_time", type_=String),  # Nullable string in 'YYYY-MM-DD HH24:MI:SS' format
        bindparam("cursor_id", type_=BigInteger)
    )

    result = await db.execute(
        query,
        {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_ids": accessible_vehicle_ids,
            "cursor_time": cursor_time,
            "cursor_id": cursor_id,
            "limit": limit + 1  # Fetch one extra to check if there are more
        }
    )

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]  # Remove the extra record

    # Convert to response objects
    data = [
        HistoryResponse(
            id=row[0],
            label=row[1],
            label2=row[2],
            obs=row[3],
            local_time=row[4],
            time_write=row[5],
            latitude=row[6],
            longitude=row[7],
            ign=row[8],
            speed=row[9],
            odom=row[10],
            rpm=row[11],
            address=row[12]
        )
        for row in rows
    ]

    # Create composite cursor for next page (partition-optimized)
    next_cursor = None
    if data and has_more:
        last_record = data[-1]
        composite_cursor = CompositeCursor(
            local_time=last_record.local_time,
            id=last_record.id
        )
        next_cursor = composite_cursor.to_string()

    return HistoryCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/history/export/csv", tags=["Reports/History"])
async def export_dev_status_csv(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export History records as CSV with PARALLEL PARTITION processing (ULTRA-FAST for large periods).

    **Parallel Partition Benefits:**
    - 20-30x faster than sequential chunks for large periods
    - Processes daily partitions (dev_status_30_pYYYYMMDD) in parallel
    - 2M records in ~30-60 seconds (vs 15-20 minutes sequential)
    - Optimized for frequent CSV exports

    **How it works:**
    1. Divides date range into daily partitions
    2. Queries up to 10 partitions simultaneously (async)
    3. Streams results in chronological order
    4. Memory efficient - processes in batches

    **Performance:**
    - 1 day: ~2-5s
    - 7 days: ~10-20s
    - 30 days: ~30-60s

    **Access Control:**
    - Only exports data from vehicles user has access to (group/subgroup)

    **Date Range:**
    - Maximum 31 days between start_date and end_date

    **Format:**
    - CSV with headers
    - UTF-8 encoding
    - Chronologically ordered (newest first)
    """
    # Validate date range (max 31 days)
    validate_date_range(start_date, end_date)

    # Check user has group access
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter for vehicles
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Get accessible vehicle IDs
    vehicle_query = select(Vehicle.id).where(access_filter)

    # Filter by specific vehicle IDs if provided
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

    # Helper: Generate daily date ranges for partition processing
    def get_daily_partitions(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Split date range into daily partitions."""
        from datetime import timedelta

        partitions = []
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end.replace(hour=23, minute=59, second=59, microsecond=999999)

        while current <= end_date:
            partition_start = max(current, start)
            partition_end = min(current + timedelta(days=1) - timedelta(microseconds=1), end)

            if partition_start <= partition_end:
                partitions.append((partition_start, partition_end))

            current += timedelta(days=1)

        return partitions

    # Helper: Query single partition
    async def query_partition(
        partition_start: datetime,
        partition_end: datetime,
        vehicle_ids_list: list[int]
    ) -> list:
        """Query data for a single daily partition."""
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            query = text("""
                SELECT
                    ds.id,
                    tu.label,
                    tu.label2,
                    tu.obs,
                    to_char(ds.local_time, 'YYYY-MM-DD HH24:MI:SS') AS local_time,
                    to_char(ds.time_write, 'YYYY-MM-DD HH24:MI:SS') AS time_write,
                    ds.latitude,
                    ds.longitude,
                    ds.ignition AS ign,
                    ds.speed,
                    COALESCE(ds.odom_total, ds.odom) AS odom,
                    ds.rpm,
                    ds.address
                FROM mova.dev_status_30 ds
                INNER JOIN mova.tracked_unit tu ON ds.unit_id = tu.id
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
        """
        Stream CSV with parallel partition processing.
        Processes up to MAX_PARALLEL_PARTITIONS daily partitions simultaneously.
        """
        import asyncio
        from app.core.logging import get_logger
        logger = get_logger(__name__)

        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Label", "Label2", "Obs", "Local Time", "Time Write",
            "Latitude", "Longitude", "Ignition", "Speed", "Odometer", "RPM", "Address"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        # Get daily partitions
        daily_partitions = get_daily_partitions(start_date, end_date)
        total_partitions = len(daily_partitions)

        logger.info(
            "csv_parallel_export_started",
            total_partitions=total_partitions,
            date_range_days=(end_date - start_date).days + 1,
            vehicle_count=len(accessible_vehicle_ids)
        )

        # Process partitions in batches (max 10 parallel to avoid DB overload)
        # Uses as_completed() for incremental streaming (memory efficient)
        MAX_PARALLEL = 10
        total_rows = 0

        for batch_start in range(0, total_partitions, MAX_PARALLEL):
            batch_end = min(batch_start + MAX_PARALLEL, total_partitions)
            batch_partitions = daily_partitions[batch_start:batch_end]

            logger.info(
                "csv_parallel_batch_processing",
                batch_num=batch_start // MAX_PARALLEL + 1,
                partitions_in_batch=len(batch_partitions)
            )

            # Launch parallel queries for this batch
            tasks = [
                query_partition(p_start, p_end, accessible_vehicle_ids)
                for p_start, p_end in batch_partitions
            ]

            # Stream results as soon as each partition completes (incremental streaming)
            # This prevents loading all partitions in memory at once
            for completed_task in asyncio.as_completed(tasks):
                partition_rows = await completed_task

                if partition_rows:
                    # Write rows from this partition IMMEDIATELY
                    for row in partition_rows:
                        writer.writerow(row)

                    total_rows += len(partition_rows)

                    # Stream to client right away (don't wait for other partitions)
                    yield output.getvalue()
                    output.truncate(0)
                    output.seek(0)

            logger.info(
                "csv_parallel_batch_completed",
                batch_num=batch_start // MAX_PARALLEL + 1,
                total_rows_so_far=total_rows
            )

        logger.info(
            "csv_parallel_export_completed",
            total_rows=total_rows,
            total_partitions=total_partitions
        )

    filename = f"history_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv_parallel(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


@router.post("/driver-km-fuel-hours/export/estimate", tags=["Reports/DriverKmFuel"])
async def estimate_driver_km_fuel_hours_export_size(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate export size and time for driver km/fuel/hours report before starting export.

    **Returns:**
    - estimated_rows: Approximate number of rows
    - estimated_size_mb: Approximate file size in MB
    - estimated_time_seconds: Estimated time in seconds

    **Use Case:**
    - Frontend can show estimation to user before export
    - Help user decide if they want to proceed
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Validate date range (convert date to datetime for validation)
    validate_date_range(datetime.combine(start_date, datetime.min.time()),
                       datetime.combine(end_date, datetime.max.time()))

    # ✅ SECURITY FIX: Use secure parameter builder
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    # Count with GROUP BY to match actual export result (aggregated rows)
    count_query = text("""
        SELECT COUNT(*) as total
        FROM (
            SELECT 1
            FROM mova.con_driver_h_km hk
            WHERE hk.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR hk.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR hk.driver_id = ANY(:driver_ids) OR hk.driver_id IS NULL)
                AND (:unit_ids IS NULL OR hk.unit_id = ANY(:unit_ids))
                AND hk.dt >= :start_date
                AND hk.dt <= :end_date
            GROUP BY hk.dt, hk.label, hk.unit_id, hk.group_id, hk.subgroup_id, hk.driver, hk.driver_id
        ) AS aggregated_data
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("driver_ids", type_=ARRAY(Integer)),
        bindparam("unit_ids", type_=ARRAY(Integer))
    )

    result = await db.execute(
        count_query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "driver_ids": driver_ids_list,
            "unit_ids": unit_ids_list,
            "start_date": start_date,
            "end_date": end_date
        }
    )

    row_count = result.scalar()

    # Estimate file size (avg 200 bytes per CSV row - larger than dev_status due to aggregations)
    estimated_size_mb = (row_count * 200) / 1024 / 1024

    # Estimate time (consolidated table is fast, ~100k rows per second)
    estimated_time_seconds = max(row_count / 100000, 1)

    logger.info(
        "driver_km_fuel_hours_export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1)
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1),
        "date_range_days": (end_date - start_date).days + 1
    }


@router.get("/driver-km-fuel-hours/cursor", response_model=DriverKmFuelHoursCursorResponse, tags=["Reports/DriverKmFuel"])
async def get_driver_km_fuel_hours_cursor(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination (base64-encoded composite cursor)"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Get driver performance metrics (km, fuel consumption, hours) from consolidated table.

    **Data Source:**
    - Table: mova.con_driver_h_km (consolidated, NOT partitioned)
    - Pre-aggregated daily metrics per driver/vehicle

    **Metrics Returned:**
    - distance_traveled_hist: Distance in kilometers
    - used_fuel_hist: Fuel consumption in liters
    - time_traveled_hist: Time traveled in hours

    **Pagination:**
    - Cursor-based pagination with composite cursor (dt + row_id)
    - Base64-encoded format: {"t": "YYYY-MM-DD", "i": row_id}
    - Orders by date DESC, then row_id (generated via ROW_NUMBER)

    **Access Control:**
    - Filters by user's group_id automatically
    - Optional filters: subgroup_ids, driver_ids, unit_ids

    **Date Range:**
    - Maximum 93 days (3 months) between start_date and end_date
    """
    # Validate date range (max 93 days for cursor endpoints)
    validate_date_range(datetime.combine(start_date, datetime.min.time()),
                       datetime.combine(end_date, datetime.max.time()),
                       max_days=MAX_DAYS_CURSOR)

    # ✅ SECURITY FIX: Use secure parameter builder
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    # Parse cursor (base64-encoded composite cursor with dt + row_id)
    cursor_dt = None
    cursor_row_id = None
    if cursor:
        try:
            # Decode composite cursor (format: {"t": "YYYY-MM-DD", "i": row_id})
            decoded_cursor = DriverReportCompositeCursor.from_string(cursor)
            cursor_dt = decoded_cursor.dt
            cursor_row_id = decoded_cursor.row_id
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format. Expected base64-encoded composite cursor."
            )

    # Build query
    # Note: Since con_driver_h_km doesn't have an id column, we'll use ROW_NUMBER()
    # to generate a consistent ordering ID based on (dt DESC, unit_id, driver_id)
    # Uses estimated data (distance_traveled_hist_estimated, used_fuel_hist_estimated)
    # when used_fuel_hist is NULL or 0, with is_estimated flag to indicate fallback
    query = text("""
        WITH ranked_data AS (
            SELECT
                hk.dt,
                hk."label",
                hk.unit_id,
                hk.group_id,
                hk.subgroup_id,
                hk.driver,
                hk.driver_id,
                CAST(CASE
                    WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0
                    THEN COALESCE(SUM(hk.distance_traveled_hist_estimated), 0)
                    ELSE SUM(hk.distance_traveled_hist)
                END / 1000 AS NUMERIC(10,2)) AS distance_traveled_hist,
                CAST(CASE
                    WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0
                    THEN COALESCE(SUM(hk.used_fuel_hist_estimated), 0)
                    ELSE SUM(hk.used_fuel_hist)
                END / 1000 AS NUMERIC(10,2)) AS used_fuel_hist,
                CAST(SUM(hk.time_traveled_hist) / 3600 AS NUMERIC(10,2)) AS time_traveled_hist,
                CAST(CASE
                    WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0
                    THEN SUM(CASE
                        WHEN hk.used_fuel_hist_estimated > 0 AND hk.used_fuel_hist_estimated < 500000
                        THEN hk.distance_traveled_hist_estimated
                        ELSE 0 END)
                    ELSE SUM(CASE
                        WHEN hk.used_fuel_hist > 0 AND hk.used_fuel_hist < 500000
                        THEN hk.distance_traveled_hist
                        ELSE 0 END)
                END / 1000 AS NUMERIC(10,2)) AS distance_traveled_hist_filtrado,
                CASE WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0 THEN true ELSE false END AS is_estimated,
                ROW_NUMBER() OVER (ORDER BY hk.dt DESC, hk.unit_id, hk.driver_id) AS row_id
            FROM
                mova.con_driver_h_km hk
            WHERE
                hk.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR hk.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR hk.driver_id = ANY(:driver_ids) OR hk.driver_id IS NULL)
                AND (:unit_ids IS NULL OR hk.unit_id = ANY(:unit_ids))
                AND hk.dt >= :start_date
                AND hk.dt <= :end_date
            GROUP BY
                hk.dt, hk."label", hk.unit_id, hk.group_id, hk.subgroup_id, hk.driver, hk.driver_id
        )
        SELECT
            dt, "label", unit_id, group_id, subgroup_id, driver, driver_id,
            distance_traveled_hist, used_fuel_hist, time_traveled_hist, distance_traveled_hist_filtrado,
            is_estimated, row_id
        FROM ranked_data
        WHERE (
            :cursor_dt IS NULL
            OR dt < CAST(:cursor_dt AS DATE)
            OR (dt = CAST(:cursor_dt AS DATE) AND row_id > :cursor_row_id)
        )
        ORDER BY dt DESC, row_id
        LIMIT :limit
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("driver_ids", type_=ARRAY(Integer)),
        bindparam("unit_ids", type_=ARRAY(Integer)),
        bindparam("cursor_dt", type_=String),
        bindparam("cursor_row_id", type_=BigInteger)
    )

    result = await db.execute(
        query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "driver_ids": driver_ids_list,
            "unit_ids": unit_ids_list,
            "start_date": start_date,
            "end_date": end_date,
            "cursor_dt": cursor_dt,
            "cursor_row_id": cursor_row_id,
            "limit": limit + 1  # Fetch one extra to check if there are more
        }
    )

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]  # Remove the extra record

    # Convert to response objects
    data = [
        DriverKmFuelHoursResponse(
            dt=row[0],
            label=row[1],
            unit_id=row[2],
            group_id=row[3],
            subgroup_id=row[4],
            driver=row[5],
            driver_id=row[6],
            distance_traveled_hist=row[7],
            used_fuel_hist=row[8],
            time_traveled_hist=row[9],
            distance_traveled_hist_filtrado=row[10],
            is_estimated=row[11]
        )
        for row in rows
    ]

    # Create cursor for next page (composite cursor: dt + row_id, base64-encoded)
    next_cursor = None
    if data and has_more:
        # Get dt and row_id from last included row
        last_dt = rows[limit - 1][0]  # dt is first column
        last_row_id = rows[limit - 1][12]  # row_id is 13th column (index 12)

        # Create composite cursor and encode to base64
        composite_cursor = DriverReportCompositeCursor(
            dt=last_dt.strftime("%Y-%m-%d") if hasattr(last_dt, 'strftime') else str(last_dt),
            row_id=last_row_id
        )
        next_cursor = composite_cursor.to_string()

    return DriverKmFuelHoursCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/driver-km-fuel-hours/export/csv", tags=["Reports/DriverKmFuel"])
async def export_driver_km_fuel_hours_csv(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export driver km/fuel/hours metrics as CSV with streaming.

    **Data Source:**
    - Table: mova.con_driver_h_km (consolidated, NOT partitioned)
    - Simple streaming query (no parallel processing needed)

    **Performance:**
    - Fast export due to consolidated/aggregated data
    - Efficient for up to 100k records
    - ~1-5 seconds for typical date ranges

    **Access Control:**
    - Only exports data from user's group
    - Optional filters: subgroup_ids, driver_ids, unit_ids

    **Date Range:**
    - Maximum 31 days between start_date and end_date

    **Format:**
    - CSV with headers
    - UTF-8 encoding
    - Chronologically ordered (newest first)
    """
    # Validate date range (convert date to datetime for validation)
    validate_date_range(datetime.combine(start_date, datetime.min.time()),
                       datetime.combine(end_date, datetime.max.time()))

    # ✅ SECURITY FIX: Use secure parameter builder
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    async def generate_csv():
        """
        Stream CSV with simple query (no parallelization needed).
        Table is NOT partitioned, so straightforward streaming is most efficient.
        """
        from app.core.logging import get_logger
        logger = get_logger(__name__)

        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Date", "Vehicle Label", "Unit ID", "Group ID", "Subgroup ID",
            "Driver Name", "Driver ID", "Distance (km)", "Fuel (L)", "Hours",
            "Filtered Distance (km)", "Is Estimated"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        logger.info(
            "driver_km_fuel_hours_csv_export_started",
            date_range_days=(end_date - start_date).days + 1
        )

        # Query with GROUP BY and aggregation
        # Uses estimated data when used_fuel_hist is NULL or 0
        query = text("""
            SELECT
                hk.dt,
                hk."label",
                hk.unit_id,
                hk.group_id,
                hk.subgroup_id,
                hk.driver,
                hk.driver_id,
                CAST(CASE
                    WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0
                    THEN COALESCE(SUM(hk.distance_traveled_hist_estimated), 0)
                    ELSE SUM(hk.distance_traveled_hist)
                END / 1000 AS NUMERIC(10,2)) AS distance_traveled_hist,
                CAST(CASE
                    WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0
                    THEN COALESCE(SUM(hk.used_fuel_hist_estimated), 0)
                    ELSE SUM(hk.used_fuel_hist)
                END / 1000 AS NUMERIC(10,2)) AS used_fuel_hist,
                CAST(SUM(hk.time_traveled_hist) / 3600 AS NUMERIC(10,2)) AS time_traveled_hist,
                CAST(CASE
                    WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0
                    THEN SUM(CASE
                        WHEN hk.used_fuel_hist_estimated > 0 AND hk.used_fuel_hist_estimated < 500000
                        THEN hk.distance_traveled_hist_estimated
                        ELSE 0 END)
                    ELSE SUM(CASE
                        WHEN hk.used_fuel_hist > 0 AND hk.used_fuel_hist < 500000
                        THEN hk.distance_traveled_hist
                        ELSE 0 END)
                END / 1000 AS NUMERIC(10,2)) AS distance_traveled_hist_filtrado,
                CASE WHEN COALESCE(SUM(hk.used_fuel_hist), 0) = 0 THEN true ELSE false END AS is_estimated
            FROM
                mova.con_driver_h_km hk
            WHERE
                hk.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR hk.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR hk.driver_id = ANY(:driver_ids) OR hk.driver_id IS NULL)
                AND (:unit_ids IS NULL OR hk.unit_id = ANY(:unit_ids))
                AND hk.dt >= :start_date
                AND hk.dt <= :end_date
            GROUP BY
                hk.dt, hk."label", hk.unit_id, hk.group_id, hk.subgroup_id, hk.driver, hk.driver_id
            ORDER BY
                hk.dt DESC
        """).bindparams(
            bindparam("group_ids", type_=ARRAY(Integer)),
            bindparam("subgroup_ids", type_=ARRAY(Integer)),
            bindparam("driver_ids", type_=ARRAY(Integer)),
            bindparam("unit_ids", type_=ARRAY(Integer))
        )

        result = await db.execute(
            query,
            {
                "group_ids": accessible_groups_list,
                "subgroup_ids": subgroup_ids_list,
                "driver_ids": driver_ids_list,
                "unit_ids": unit_ids_list,
                "start_date": start_date,
                "end_date": end_date
            }
        )

        # Stream all rows
        total_rows = 0
        chunk_size = 1000

        while True:
            rows = result.fetchmany(chunk_size)
            if not rows:
                break

            for row in rows:
                writer.writerow(row)
                total_rows += 1

            yield output.getvalue()
            output.truncate(0)
            output.seek(0)

        logger.info(
            "driver_km_fuel_hours_csv_export_completed",
            total_rows=total_rows
        )

    filename = f"driver_km_fuel_hours_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


@router.post("/telemetry/export/estimate", tags=["Reports/Telemetry"])
async def estimate_telemetry_export_size(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate telemetry export size and time before starting the actual export.

    **Returns:**
    - estimated_rows: Approximate number of rows
    - estimated_size_mb: Approximate file size in MB
    - estimated_time_seconds: Estimated time in seconds
    - recommended_method: 'streaming' or 'background'

    **Use Case:**
    - Frontend can show estimation to user before export
    - Choose appropriate export method based on size
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
    vehicle_query = select(Vehicle.id).where(access_filter)

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
            "recommended_method": "none"
        }

    # Fast count using PostgreSQL COUNT
    count_query = text("""
        SELECT COUNT(*) as total
        FROM mova.con_telemetry ct
        WHERE ct.start_time >= :start_date
            AND ct.start_time < :end_date
            AND ct.unit_id = ANY(:vehicle_ids)
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

    # Estimate file size (avg 300 bytes per CSV row - larger due to many telemetry fields)
    estimated_size_mb = (row_count * 300) / 1024 / 1024

    # Estimate time based on partition performance
    # ~50k rows per partition per second (monthly partitions)
    estimated_time_seconds = max(row_count / 50000, 2)

    # Recommend method
    if estimated_time_seconds < 60:
        recommended_method = "streaming"  # < 1 min
    elif estimated_time_seconds < 300:
        recommended_method = "streaming"  # < 5 min
    else:
        recommended_method = "background"  # > 5 min

    logger.info(
        "telemetry_export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1),
        recommended_method=recommended_method
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1),
        "recommended_method": recommended_method,
        "date_range_days": (end_date - start_date).days + 1,
        "vehicle_count": len(accessible_vehicle_ids)
    }


@router.get("/telemetry/cursor", response_model=TelemetryCursorResponse, tags=["Reports/Telemetry"])
async def list_telemetry_cursor(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    cursor: Optional[str] = Query(None, description="Composite cursor for partition-optimized pagination (base64-encoded)"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    List Telemetry records with partition-optimized cursor pagination (monthly partitions).

    **Partition Optimization:**
    - Uses composite cursor (start_time, id) for automatic partition pruning
    - PostgreSQL skips irrelevant monthly partitions (con_telemetry_pYYYYMM)
    - Efficient for large date ranges

    **Cursor Pagination Benefits:**
    - Efficient for large datasets (no OFFSET overhead)
    - Consistent results even with concurrent inserts
    - Fast performance regardless of page depth

    **Usage:**
    1. First request: omit cursor parameter
    2. Subsequent requests: use `next_cursor` from previous response (base64-encoded)
    3. Continue until `has_more` is false

    **Access Control:**
    - Only returns data from vehicles user has access to (group/subgroup)

    **Date Range:**
    - Maximum 93 days (3 months) between start_date and end_date
    """
    # Validate date range (max 93 days for cursor endpoints)
    validate_date_range(start_date, end_date, max_days=MAX_DAYS_CURSOR)

    # Check user has group access
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter for vehicles
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Get accessible vehicle IDs
    vehicle_query = select(Vehicle.id).where(access_filter)

    # Filter by specific vehicle IDs if provided
    if vehicle_ids:
        requested_ids = [int(vid.strip()) for vid in vehicle_ids.split(",")]
        vehicle_query = vehicle_query.where(Vehicle.id.in_(requested_ids))

    vehicle_result = await db.execute(vehicle_query)
    accessible_vehicle_ids = [row[0] for row in vehicle_result.fetchall()]

    if not accessible_vehicle_ids:
        return TelemetryCursorResponse(
            data=[],
            next_cursor=None,
            has_more=False,
            total_returned=0
        )

    # Decode composite cursor if provided
    cursor_time = None
    cursor_id = None
    if cursor:
        try:
            # Composite cursor format (base64)
            decoded_cursor = TelemetryCompositeCursor.from_string(cursor)
            cursor_time = decoded_cursor.start_time
            cursor_id = decoded_cursor.id
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format. Expected base64-encoded composite cursor."
            )

    # Build query with partition-optimized composite cursor
    # Using raw SQL for optimal performance with JOIN and formatting
    query = text("""
        SELECT
            ct.id,
            ct.trip_id,
            ct.trip_number,
            ct.unit_id,
            ct.unit_label,
            ct.device_id,
            ct.driver_id,
            ct.driver_name,
            to_char(ct.start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
            to_char(ct.end_time, 'YYYY-MM-DD HH24:MI:SS') AS end_time,
            ct.total_time,
            ct.time_moving,
            ct.time_stopped,
            ct.start_odometer,
            ct.end_odometer,
            ct.distance_traveled,
            ct.start_lat,
            ct.start_lon,
            ct.start_poi_name,
            ct.start_area_name,
            ct.end_lat,
            ct.end_lon,
            ct.end_poi_name,
            ct.end_area_name,
            ct.max_speed,
            ct.avg_speed,
            ct.count_over_speed,
            ct.time_over_speed,
            ct.fuel_used,
            ct.efficiency_kml,
            ct.count_hard_acel,
            ct.count_hard_brake,
            ct.count_harsh_turn,
            ct.max_rpm_permitted,
            ct.time_over_rpm,
            ct.time_stop_engine_on,
            ct.count_stop_engine_on,
            -- Faixas de condução. Estavam no banco desde sempre e o schema não
            -- as expunha; sem elas não há como avaliar condução por viagem.
            ct.time_blue, ct.count_blue,
            ct.time_green, ct.count_green,
            ct.time_extra_eco, ct.count_extra_eco,
            ct.time_yellow, ct.count_yellow,
            ct.time_red, ct.count_red,
            ct.time_inercia, ct.time_eco_roll, ct.time_retarder,
            ct.time_autopilot, ct.time_banguela, ct.count_banguela,
            ct.time_low_speed, ct.time_tolerancia,
            ct.distance_pulling, ct.distance_simple_inertia,
            ct.distance_retarder, ct.distance_ecoroll, ct.distance_autopilot,
            -- Chuva: o equipamento distingue, e é o fator que mais distorce
            -- comparação de consumo entre períodos.
            ct.time_raining, ct.time_dry,
            -- Velocidade separada por tipo de via e condição de pista.
            ct.max_urban_permitted_speed, ct.max_road_permitted_speed,
            ct.max_road_rain_speed, ct.urban_rain_speed,
            ct.time_over_urban_speed, ct.time_over_road_speed,
            ct.count_over_urban_speed, ct.count_over_road_speed,
            ct.count_speed_violation_l2, ct.count_speed_violation_l3,
            -- Linha e jornada, quando o equipamento envia.
            ct.line, ct.line_number, ct.trip_direction,
            ct.journey_status, ct.journey_opening_date,
            -- Ponto e cerca de origem e destino, com a distância registrada.
            ct.start_poi_id, ct.start_poi_distance, ct.start_area_id,
            ct.end_poi_id, ct.end_poi_distance, ct.end_area_id,
            -- Horímetro e combustível bruto: base do gatilho por horas da
            -- manutenção preventiva e da conferência de abastecimento.
            ct.start_hourmeter, ct.end_hourmeter,
            ct.start_fuel, ct.end_fuel, ct.fuel_used_stopped,
            -- Carga do motor e turbo.
            ct.time_engine_load_level1, ct.time_engine_load_level2,
            ct.time_engine_load_level3,
            ct.time_over_turbo_pressure, ct.time_under_turbo_pressure,
            ct.count_clutch, ct.count_cluth_excess, ct.time_cluth_excess,
            ct.count_stop_accel, ct.time_stop_accel,
            ct.time_stop_engine_on_productive, ct.time_engine_off,
            ct.reached_rpm,
            ct.time_over_urban_rain_speed, ct.time_over_road_rain_speed,
            ct.count_over_urban_rain_speed, ct.count_over_road_rain_speed,
            ct.reached_over_urban_speed, ct.reached_over_road_speed,
            ct.reached_over_urban_rain_speed, ct.reached_over_road_rain_speed,
            ct.trip_status, ct.trip_opening_date
        FROM mova.con_telemetry ct
        INNER JOIN mova.tracked_unit tu ON ct.unit_id = tu.id
        WHERE ct.start_time >= :start_date
            AND ct.start_time < :end_date
            AND tu.id = ANY(:vehicle_ids)
            AND (
                :cursor_time IS NULL
                OR ct.start_time < CAST(:cursor_time AS timestamp)
                OR (ct.start_time = CAST(:cursor_time AS timestamp) AND ct.id < :cursor_id)
            )
        ORDER BY ct.start_time DESC, ct.id DESC
        LIMIT :limit
    """).bindparams(
        bindparam("vehicle_ids", type_=ARRAY(Integer)),
        bindparam("cursor_time", type_=String),
        bindparam("cursor_id", type_=BigInteger)
    )

    result = await db.execute(
        query,
        {
            "start_date": start_date,
            "end_date": end_date,
            "vehicle_ids": accessible_vehicle_ids,
            "cursor_time": cursor_time,
            "cursor_id": cursor_id,
            "limit": limit + 1  # Fetch one extra to check if there are more
        }
    )

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]  # Remove the extra record

    # Convert to response objects
    data = [
        TelemetryResponse(
            id=row[0],
            trip_id=row[1],
            trip_number=row[2],
            unit_id=row[3],
            unit_label=row[4],
            device_id=row[5],
            driver_id=row[6],
            driver_name=row[7],
            start_time=row[8],
            end_time=row[9],
            total_time=row[10],
            time_moving=row[11],
            time_stopped=row[12],
            start_odometer=row[13],
            end_odometer=row[14],
            distance_traveled=row[15],
            start_lat=row[16],
            start_lon=row[17],
            start_poi_name=row[18],
            start_area_name=row[19],
            end_lat=row[20],
            end_lon=row[21],
            end_poi_name=row[22],
            end_area_name=row[23],
            max_speed=row[24],
            avg_speed=row[25],
            count_over_speed=row[26],
            time_over_speed=row[27],
            fuel_used=row[28],
            efficiency_kml=row[29],
            count_hard_acel=row[30],
            count_hard_brake=row[31],
            count_harsh_turn=row[32],
            max_rpm_permitted=row[33],
            time_over_rpm=row[34],
            time_stop_engine_on=row[35],
            count_stop_engine_on=row[36]
        )
        for row in rows
    ]

    # Create composite cursor for next page (partition-optimized)
    next_cursor = None
    if data and has_more:
        last_record = data[-1]
        composite_cursor = TelemetryCompositeCursor(
            start_time=last_record.start_time,
            id=last_record.id
        )
        next_cursor = composite_cursor.to_string()

    return TelemetryCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/telemetry/export/csv", tags=["Reports/Telemetry"])
async def export_telemetry_csv(
    start_date: datetime = Query(..., description="Start date (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End date (YYYY-MM-DD HH:MM:SS)"),
    vehicle_ids: Optional[str] = Query(None, description="Comma-separated vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export Telemetry records as CSV with PARALLEL PARTITION processing (monthly partitions).

    **Parallel Partition Benefits:**
    - Processes monthly partitions (con_telemetry_pYYYYMM) in parallel
    - Efficient for large date ranges
    - Memory efficient - streams results

    **How it works:**
    1. Divides date range into monthly partitions
    2. Queries up to 10 partitions simultaneously (async)
    3. Streams results in chronological order
    4. Memory efficient - processes in batches

    **Access Control:**
    - Only exports data from vehicles user has access to (group/subgroup)

    **Date Range:**
    - Maximum 31 days between start_date and end_date

    **Format:**
    - CSV with headers
    - UTF-8 encoding
    - Chronologically ordered (newest first)
    """
    # Validate date range (max 31 days)
    validate_date_range(start_date, end_date)

    # Check user has group access
    if not current_user.group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # Build access filter for vehicles
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Get accessible vehicle IDs
    vehicle_query = select(Vehicle.id).where(access_filter)

    # Filter by specific vehicle IDs if provided
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

    # Helper: Generate monthly date ranges for partition processing
    def get_monthly_partitions(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Split date range into monthly partitions."""
        from datetime import timedelta
        from calendar import monthrange

        partitions = []
        current = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = end

        while current <= end_date:
            # Get last day of current month
            last_day = monthrange(current.year, current.month)[1]
            month_end = current.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)

            partition_start = max(current, start)
            partition_end = min(month_end, end)

            if partition_start <= partition_end:
                partitions.append((partition_start, partition_end))

            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return partitions

    # Helper: Query single partition
    async def query_partition(
        partition_start: datetime,
        partition_end: datetime,
        vehicle_ids_list: list[int]
    ) -> list:
        """Query data for a single monthly partition."""
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            query = text("""
                SELECT
                    ct.id,
                    ct.trip_id,
                    ct.trip_number,
                    ct.unit_id,
                    ct.unit_label,
                    ct.device_id,
                    ct.driver_id,
                    ct.driver_name,
                    to_char(ct.start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
                    to_char(ct.end_time, 'YYYY-MM-DD HH24:MI:SS') AS end_time,
                    ct.total_time,
                    ct.time_moving,
                    ct.time_stopped,
                    ct.start_odometer,
                    ct.end_odometer,
                    ct.distance_traveled,
                    ct.start_lat,
                    ct.start_lon,
                    ct.start_poi_name,
                    ct.start_area_name,
                    ct.end_lat,
                    ct.end_lon,
                    ct.end_poi_name,
                    ct.end_area_name,
                    ct.max_speed,
                    ct.avg_speed,
                    ct.count_over_speed,
                    ct.time_over_speed,
                    ct.fuel_used,
                    ct.efficiency_kml,
                    ct.count_hard_acel,
                    ct.count_hard_brake,
                    ct.count_harsh_turn,
                    ct.max_rpm_permitted,
                    ct.time_over_rpm,
                    ct.time_stop_engine_on,
                    ct.count_stop_engine_on
                FROM mova.con_telemetry ct
                WHERE ct.start_time >= :partition_start
                    AND ct.start_time <= :partition_end
                    AND ct.unit_id = ANY(:vehicle_ids)
                ORDER BY ct.start_time DESC, ct.id DESC
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
        """
        Stream CSV with parallel partition processing.
        Processes up to MAX_PARALLEL_PARTITIONS monthly partitions simultaneously.
        """
        import asyncio
        from app.core.logging import get_logger
        logger = get_logger(__name__)

        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Trip ID", "Trip Number", "Unit ID", "Unit Label", "Device ID",
            "Driver ID", "Driver Name", "Start Time", "End Time", "Total Time (s)",
            "Time Moving (s)", "Time Stopped (s)", "Start Odometer", "End Odometer",
            "Distance Traveled (km)", "Start Lat", "Start Lon", "Start POI", "Start Area",
            "End Lat", "End Lon", "End POI", "End Area", "Max Speed", "Avg Speed",
            "Count Over Speed", "Time Over Speed (s)", "Fuel Used (L)", "Efficiency (km/L)",
            "Hard Accel Count", "Hard Brake Count", "Harsh Turn Count",
            "Max RPM Permitted", "Time Over RPM (s)", "Time Stop Engine On (s)",
            "Count Stop Engine On"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        # Get monthly partitions
        monthly_partitions = get_monthly_partitions(start_date, end_date)
        total_partitions = len(monthly_partitions)

        logger.info(
            "telemetry_csv_parallel_export_started",
            total_partitions=total_partitions,
            date_range_days=(end_date - start_date).days + 1,
            vehicle_count=len(accessible_vehicle_ids)
        )

        # Process partitions in batches (max 10 parallel to avoid DB overload)
        MAX_PARALLEL = 10
        total_rows = 0

        for batch_start in range(0, total_partitions, MAX_PARALLEL):
            batch_end = min(batch_start + MAX_PARALLEL, total_partitions)
            batch_partitions = monthly_partitions[batch_start:batch_end]

            logger.info(
                "telemetry_csv_parallel_batch_processing",
                batch_num=batch_start // MAX_PARALLEL + 1,
                partitions_in_batch=len(batch_partitions)
            )

            # Launch parallel queries for this batch
            tasks = [
                query_partition(p_start, p_end, accessible_vehicle_ids)
                for p_start, p_end in batch_partitions
            ]

            # Stream results as soon as each partition completes
            for completed_task in asyncio.as_completed(tasks):
                partition_rows = await completed_task

                if partition_rows:
                    # Write rows from this partition IMMEDIATELY
                    for row in partition_rows:
                        writer.writerow(row)

                    total_rows += len(partition_rows)

                    # Stream to client right away
                    yield output.getvalue()
                    output.truncate(0)
                    output.seek(0)

            logger.info(
                "telemetry_csv_parallel_batch_completed",
                batch_num=batch_start // MAX_PARALLEL + 1,
                total_rows_so_far=total_rows
            )

        logger.info(
            "telemetry_csv_parallel_export_completed",
            total_rows=total_rows,
            total_partitions=total_partitions
        )

    filename = f"telemetry_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv_parallel(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


# ============================================================================
# RPM Band Time (Tempo de Faixa) Endpoints
# ============================================================================

@router.post("/rpm-band-time/export/estimate", tags=["Reports/RpmBandTime"])
async def estimate_rpm_band_time_export_size(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate export size and time for RPM band time report before starting export.

    **Returns:**
    - estimated_rows: Approximate number of rows
    - estimated_size_mb: Approximate file size in MB
    - estimated_time_seconds: Estimated time in seconds
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Validate date range
    validate_date_range(datetime.combine(start_date, datetime.min.time()),
                       datetime.combine(end_date, datetime.max.time()))

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    # Count with GROUP BY to match actual export result
    count_query = text("""
        SELECT COUNT(*) as total
        FROM (
            SELECT 1
            FROM mova.con_telemetry_day ctd
            WHERE ctd.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR ctd.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR ctd.driver_id = ANY(:driver_ids) OR ctd.driver_id IS NULL)
                AND (:unit_ids IS NULL OR ctd.unit_id = ANY(:unit_ids))
                AND ctd."day" >= :start_date
                AND ctd."day" <= :end_date
            GROUP BY ctd.unit_id, ctd.group_id, ctd.subgroup_id, ctd.driver_id, ctd."day"
        ) AS aggregated_data
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("driver_ids", type_=ARRAY(Integer)),
        bindparam("unit_ids", type_=ARRAY(Integer))
    )

    result = await db.execute(
        count_query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "driver_ids": driver_ids_list,
            "unit_ids": unit_ids_list,
            "start_date": start_date,
            "end_date": end_date
        }
    )

    row_count = result.scalar()

    # Estimate file size (avg 150 bytes per CSV row)
    estimated_size_mb = (row_count * 150) / 1024 / 1024

    # Estimate time (~100k rows per second)
    estimated_time_seconds = max(row_count / 100000, 1)

    logger.info(
        "rpm_band_time_export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1)
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1),
        "date_range_days": (end_date - start_date).days + 1
    }


@router.get("/rpm-band-time/cursor", response_model=RpmBandTimeCursorResponse, tags=["Reports/RpmBandTime"])
async def get_rpm_band_time_cursor(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Get RPM band time metrics from con_telemetry_day table.

    **Data Source:**
    - Table: mova.con_telemetry_day (consolidated daily telemetry)

    **Metrics Returned:**
    - stop_engine_on: Time stopped with engine on (seconds)
    - blue: Time in blue/economical RPM band (seconds)
    - green: Time in green/optimal RPM band (seconds)
    - yellow: Time in yellow/warning RPM band (seconds)
    - red: Time in red/excess RPM band (seconds)
    - inercia: Time coasting/inertia (seconds)
    - total_time: Sum of all times (seconds)

    **Pagination:**
    - Cursor-based pagination with composite cursor (day + row_id)

    **Date Range:**
    - Maximum 93 days (3 months) between start_date and end_date
    """
    # Validate date range (max 93 days for cursor endpoints)
    validate_date_range(datetime.combine(start_date, datetime.min.time()),
                       datetime.combine(end_date, datetime.max.time()),
                       max_days=MAX_DAYS_CURSOR)

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    # Parse cursor
    cursor_dt = None
    cursor_row_id = None
    if cursor:
        try:
            decoded_cursor = DriverReportCompositeCursor.from_string(cursor)
            cursor_dt = decoded_cursor.dt
            cursor_row_id = decoded_cursor.row_id
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format."
            )

    # Build query
    query = text("""
        WITH ranked_data AS (
            SELECT
                ctd."day",
                ctd.unit_id,
                ctd.group_id,
                ctd.subgroup_id,
                ctd.driver_id,
                -- DS-1396/DS-1497, como no CodeCommit e no BI (vault:
                -- nomes-diferentes-faixas-bi-vs-legado): verde sem extra
                -- econômica, e total_time = soma das 11 colunas. Antes o verde
                -- vinha somado à extra econômica e o total deixava de fora
                -- parado acelerando, movimento sem tração e tolerância, o que
                -- inflava todo percentual calculado sobre ele.
                COALESCE(SUM(ctd.time_stop_engine_on), 0) + COALESCE(SUM(ctd.time_stop_engine_on_productive), 0) AS stop_engine_on,
                COALESCE(SUM(ctd.time_stop_accel), 0) AS parado_acelerando,
                COALESCE(SUM(ctd.time_banguela), 0) AS movimento_sem_tracao,
                COALESCE(SUM(ctd.time_blue), 0) AS blue,
                COALESCE(SUM(ctd.time_green), 0) AS green,
                COALESCE(SUM(ctd.time_extra_eco), 0) AS extra_economica,
                COALESCE(SUM(ctd.time_yellow), 0) AS yellow,
                COALESCE(SUM(ctd.time_red), 0) AS red,
                COALESCE(SUM(ctd.time_inercia), 0) AS inercia,
                COALESCE(SUM(ctd.time_tolerancia), 0) AS tolerancia,
                COALESCE(SUM(ctd.time_stop_engine_on), 0)
                    + COALESCE(SUM(ctd.time_stop_engine_on_productive), 0)
                    + COALESCE(SUM(ctd.time_stop_accel), 0)
                    + COALESCE(SUM(ctd.time_banguela), 0)
                    + COALESCE(SUM(ctd.time_blue), 0)
                    + COALESCE(SUM(ctd.time_green), 0)
                    + COALESCE(SUM(ctd.time_extra_eco), 0)
                    + COALESCE(SUM(ctd.time_yellow), 0)
                    + COALESCE(SUM(ctd.time_red), 0)
                    + COALESCE(SUM(ctd.time_inercia), 0)
                    + COALESCE(SUM(ctd.time_tolerancia), 0) AS total_time,
                ROW_NUMBER() OVER (ORDER BY ctd."day" DESC, ctd.unit_id, ctd.driver_id) AS row_id
            FROM
                mova.con_telemetry_day ctd
            WHERE
                ctd.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR ctd.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR ctd.driver_id = ANY(:driver_ids) OR ctd.driver_id IS NULL)
                AND (:unit_ids IS NULL OR ctd.unit_id = ANY(:unit_ids))
                AND ctd."day" >= :start_date
                AND ctd."day" <= :end_date
            GROUP BY
                ctd.unit_id, ctd.group_id, ctd.subgroup_id, ctd.driver_id, ctd."day"
        )
        SELECT
            "day", unit_id, group_id, subgroup_id, driver_id,
            stop_engine_on, blue, green, yellow, red, inercia, total_time, row_id,
            parado_acelerando, movimento_sem_tracao, extra_economica, tolerancia
        FROM ranked_data
        WHERE (
            :cursor_dt IS NULL
            OR "day" < CAST(:cursor_dt AS DATE)
            OR ("day" = CAST(:cursor_dt AS DATE) AND row_id > :cursor_row_id)
        )
        ORDER BY "day" DESC, row_id
        LIMIT :limit
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("driver_ids", type_=ARRAY(Integer)),
        bindparam("unit_ids", type_=ARRAY(Integer)),
        bindparam("cursor_dt", type_=String),
        bindparam("cursor_row_id", type_=BigInteger)
    )

    result = await db.execute(
        query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "driver_ids": driver_ids_list,
            "unit_ids": unit_ids_list,
            "start_date": start_date,
            "end_date": end_date,
            "cursor_dt": cursor_dt,
            "cursor_row_id": cursor_row_id,
            "limit": limit + 1
        }
    )

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Convert to response objects
    data = [
        RpmBandTimeResponse(
            day=row[0],
            unit_id=row[1],
            group_id=row[2],
            subgroup_id=row[3],
            driver_id=row[4],
            stop_engine_on=row[5] or 0,
            blue=row[6] or 0,
            green=row[7] or 0,
            yellow=row[8] or 0,
            red=row[9] or 0,
            inercia=row[10] or 0,
            total_time=row[11] or 0,
            # Colunas novas no fim do SELECT, para os índices de antes (e o
            # row_id do cursor, em 12) não mudarem.
            parado_acelerando=row[13] or 0,
            movimento_sem_tracao=row[14] or 0,
            extra_economica=row[15] or 0,
            tolerancia=row[16] or 0,
        )
        for row in rows
    ]

    # Create cursor for next page
    next_cursor = None
    if data and has_more:
        last_day = rows[limit - 1][0]
        last_row_id = rows[limit - 1][12]
        composite_cursor = DriverReportCompositeCursor(
            dt=last_day.strftime("%Y-%m-%d") if hasattr(last_day, 'strftime') else str(last_day),
            row_id=last_row_id
        )
        next_cursor = composite_cursor.to_string()

    return RpmBandTimeCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/rpm-band-time/export/csv", tags=["Reports/RpmBandTime"])
async def export_rpm_band_time_csv(
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export RPM band time data as streaming CSV.

    **Data Source:**
    - Table: mova.con_telemetry_day

    **Features:**
    - Memory efficient streaming
    - Download starts immediately
    - Maximum 31 days range
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Validate date range
    validate_date_range(datetime.combine(start_date, datetime.min.time()),
                       datetime.combine(end_date, datetime.max.time()))

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    async def generate_csv():
        """Stream CSV with aggregated RPM band time data."""
        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Date", "Unit ID", "Group ID", "Subgroup ID", "Driver ID",
            "Stop Engine On (s)", "Blue (s)", "Green (s)", "Yellow (s)", "Red (s)", "Inertia (s)", "Total (s)"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        logger.info(
            "rpm_band_time_csv_export_started",
            date_range_days=(end_date - start_date).days + 1
        )

        # Query
        query = text("""
            SELECT
                ctd."day",
                ctd.unit_id,
                ctd.group_id,
                ctd.subgroup_id,
                ctd.driver_id,
                COALESCE(SUM(ctd.time_stop_engine_on), 0) + COALESCE(SUM(ctd.time_stop_engine_on_productive), 0) AS stop_engine_on,
                COALESCE(SUM(ctd.time_blue), 0) AS blue,
                COALESCE(SUM(ctd.time_green), 0) + COALESCE(SUM(ctd.time_extra_eco), 0) AS green,
                COALESCE(SUM(ctd.time_yellow), 0) AS yellow,
                COALESCE(SUM(ctd.time_red), 0) AS red,
                COALESCE(SUM(ctd.time_inercia), 0) AS inercia,
                COALESCE(SUM(ctd.time_stop_engine_on), 0)
                    + COALESCE(SUM(ctd.time_stop_engine_on_productive), 0)
                    + COALESCE(SUM(ctd.time_blue), 0)
                    + COALESCE(SUM(ctd.time_green), 0)
                    + COALESCE(SUM(ctd.time_yellow), 0)
                    + COALESCE(SUM(ctd.time_red), 0)
                    + COALESCE(SUM(ctd.time_inercia), 0)
                    + COALESCE(SUM(ctd.time_extra_eco), 0) AS total_time
            FROM
                mova.con_telemetry_day ctd
            WHERE
                ctd.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR ctd.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR ctd.driver_id = ANY(:driver_ids) OR ctd.driver_id IS NULL)
                AND (:unit_ids IS NULL OR ctd.unit_id = ANY(:unit_ids))
                AND ctd."day" >= :start_date
                AND ctd."day" <= :end_date
            GROUP BY
                ctd.unit_id, ctd.group_id, ctd.subgroup_id, ctd.driver_id, ctd."day"
            ORDER BY
                ctd."day" DESC
        """).bindparams(
            bindparam("group_ids", type_=ARRAY(Integer)),
            bindparam("subgroup_ids", type_=ARRAY(Integer)),
            bindparam("driver_ids", type_=ARRAY(Integer)),
            bindparam("unit_ids", type_=ARRAY(Integer))
        )

        result = await db.execute(
            query,
            {
                "group_ids": accessible_groups_list,
                "subgroup_ids": subgroup_ids_list,
                "driver_ids": driver_ids_list,
                "unit_ids": unit_ids_list,
                "start_date": start_date,
                "end_date": end_date
            }
        )

        # Stream rows
        total_rows = 0
        chunk_size = 1000

        while True:
            rows = result.fetchmany(chunk_size)
            if not rows:
                break

            for row in rows:
                writer.writerow(row)
                total_rows += 1

            yield output.getvalue()
            output.truncate(0)
            output.seek(0)

        logger.info(
            "rpm_band_time_csv_export_completed",
            total_rows=total_rows
        )

    filename = f"rpm_band_time_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


# ============================================================================
# Heatmap (Mapa de Calor) Endpoints
# ============================================================================

@router.post("/heatmap/export/estimate", tags=["Reports/Heatmap"])
async def estimate_heatmap_export_size(
    start_date: datetime = Query(..., description="Start datetime (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End datetime (YYYY-MM-DD HH:MM:SS)"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate export size and time for heatmap report before starting export.

    **Returns:**
    - estimated_rows: Approximate number of rows
    - estimated_size_mb: Approximate file size in MB
    - estimated_time_seconds: Estimated time in seconds
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Validate date range
    validate_date_range(start_date, end_date)

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    # Count with GROUP BY
    count_query = text("""
        SELECT COUNT(*) as total
        FROM (
            SELECT 1
            FROM mova.heatmap h
            WHERE h.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR h.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR h.driver_id = ANY(:driver_ids) OR h.driver_id IS NULL)
                AND (:unit_ids IS NULL OR h.unit_id = ANY(:unit_ids))
                AND h.local_time >= :start_date
                AND h.local_time <= :end_date
            GROUP BY DATE_TRUNC('hour', h.local_time), h.unit_id, h."label", h.label2, h.group_id, h.subgroup_id, h.driver_id, h.driver_name
        ) AS aggregated_data
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("driver_ids", type_=ARRAY(Integer)),
        bindparam("unit_ids", type_=ARRAY(Integer))
    )

    result = await db.execute(
        count_query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "driver_ids": driver_ids_list,
            "unit_ids": unit_ids_list,
            "start_date": start_date,
            "end_date": end_date
        }
    )

    row_count = result.scalar()

    # Estimate file size (avg 200 bytes per CSV row)
    estimated_size_mb = (row_count * 200) / 1024 / 1024

    # Estimate time
    estimated_time_seconds = max(row_count / 50000, 1)

    logger.info(
        "heatmap_export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1)
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1),
        "date_range_days": (end_date - start_date).days + 1
    }


@router.get("/heatmap/cursor", response_model=HeatmapCursorResponse, tags=["Reports/Heatmap"])
async def get_heatmap_cursor(
    start_date: datetime = Query(..., description="Start datetime (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End datetime (YYYY-MM-DD HH:MM:SS)"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Get heatmap event counts aggregated by hour from heatmap table.

    **Data Source:**
    - Table: mova.heatmap

    **Metrics Returned (event counts per hour):**
    - faixa_amarela: Yellow RPM band events
    - faixa_vermelha: Red RPM band events
    - batendo_transmissao: Transmission hitting events
    - parado_acelerando: Stopped accelerating events
    - excesso_velocidade: Speeding events

    **Pagination:**
    - Cursor-based pagination with composite cursor (datetime + row_id)

    **Date Range:**
    - Maximum 93 days (3 months) between start_date and end_date
    """
    # Validate date range (max 93 days for cursor endpoints)
    validate_date_range(start_date, end_date, max_days=MAX_DAYS_CURSOR)

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    # Parse cursor
    cursor_dt = None
    cursor_row_id = None
    if cursor:
        try:
            decoded_cursor = DriverReportCompositeCursor.from_string(cursor)
            cursor_dt = decoded_cursor.dt
            cursor_row_id = decoded_cursor.row_id
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format."
            )

    # Build query
    query = text("""
        WITH ranked_data AS (
            SELECT
                TO_CHAR(DATE_TRUNC('hour', h.local_time), 'YYYY-MM-DD HH24:00') AS data_hora,
                h.unit_id,
                h."label",
                h.label2,
                h.group_id,
                h.subgroup_id,
                h.driver_id,
                h.driver_name,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%FAIXA AMARELA%' THEN 1 END) AS faixa_amarela,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%FAIXA VERMELHA%' THEN 1 END) AS faixa_vermelha,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%BATENDO TRANSMISSÃO%' THEN 1 END) AS batendo_transmissao,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%PARADO ACELERANDO%' THEN 1 END) AS parado_acelerando,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%EXCESSO DE VELOCIDADE%' THEN 1 END) AS excesso_velocidade,
                ROW_NUMBER() OVER (ORDER BY DATE_TRUNC('hour', h.local_time) DESC, h.unit_id, h.driver_id) AS row_id
            FROM
                mova.heatmap h
            WHERE
                h.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR h.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR h.driver_id = ANY(:driver_ids) OR h.driver_id IS NULL)
                AND (:unit_ids IS NULL OR h.unit_id = ANY(:unit_ids))
                AND h.local_time >= :start_date
                AND h.local_time <= :end_date
            GROUP BY
                DATE_TRUNC('hour', h.local_time),
                h.unit_id,
                h."label",
                h.label2,
                h.group_id,
                h.subgroup_id,
                h.driver_id,
                h.driver_name
        )
        SELECT
            data_hora, unit_id, "label", label2, group_id, subgroup_id, driver_id, driver_name,
            faixa_amarela, faixa_vermelha, batendo_transmissao, parado_acelerando, excesso_velocidade, row_id
        FROM ranked_data
        WHERE (
            :cursor_dt IS NULL
            OR data_hora < :cursor_dt
            OR (data_hora = :cursor_dt AND row_id > :cursor_row_id)
        )
        ORDER BY data_hora DESC, row_id
        LIMIT :limit
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("driver_ids", type_=ARRAY(Integer)),
        bindparam("unit_ids", type_=ARRAY(Integer)),
        bindparam("cursor_dt", type_=String),
        bindparam("cursor_row_id", type_=BigInteger)
    )

    result = await db.execute(
        query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "driver_ids": driver_ids_list,
            "unit_ids": unit_ids_list,
            "start_date": start_date,
            "end_date": end_date,
            "cursor_dt": cursor_dt,
            "cursor_row_id": cursor_row_id,
            "limit": limit + 1
        }
    )

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Convert to response objects
    data = [
        HeatmapResponse(
            data_hora=row[0],
            unit_id=row[1],
            label=row[2],
            label2=row[3],
            group_id=row[4],
            subgroup_id=row[5],
            driver_id=row[6],
            driver_name=row[7],
            faixa_amarela=row[8] or 0,
            faixa_vermelha=row[9] or 0,
            batendo_transmissao=row[10] or 0,
            parado_acelerando=row[11] or 0,
            excesso_velocidade=row[12] or 0
        )
        for row in rows
    ]

    # Create cursor for next page
    next_cursor = None
    if data and has_more:
        last_data_hora = rows[limit - 1][0]
        last_row_id = rows[limit - 1][13]
        composite_cursor = DriverReportCompositeCursor(
            dt=last_data_hora,
            row_id=last_row_id
        )
        next_cursor = composite_cursor.to_string()

    return HeatmapCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/heatmap/export/csv", tags=["Reports/Heatmap"])
async def export_heatmap_csv(
    start_date: datetime = Query(..., description="Start datetime (YYYY-MM-DD HH:MM:SS)"),
    end_date: datetime = Query(..., description="End datetime (YYYY-MM-DD HH:MM:SS)"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    driver_ids: Optional[str] = Query(None, description="Comma-separated driver IDs"),
    unit_ids: Optional[str] = Query(None, description="Comma-separated unit/vehicle IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export heatmap event data as streaming CSV.

    **Data Source:**
    - Table: mova.heatmap

    **Features:**
    - Memory efficient streaming
    - Download starts immediately
    - Maximum 31 days range
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Validate date range
    validate_date_range(start_date, end_date)

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            driver_ids,
            unit_ids
        )

    async def generate_csv():
        """Stream CSV with heatmap data."""
        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Date/Hour", "Unit ID", "Label", "Label2", "Group ID", "Subgroup ID",
            "Driver ID", "Driver Name", "Yellow Band", "Red Band",
            "Transmission Hitting", "Stopped Accelerating", "Speeding"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        logger.info(
            "heatmap_csv_export_started",
            date_range_days=(end_date - start_date).days + 1
        )

        # Query
        query = text("""
            SELECT
                TO_CHAR(DATE_TRUNC('hour', h.local_time), 'YYYY-MM-DD HH24:00') AS data_hora,
                h.unit_id,
                h."label",
                h.label2,
                h.group_id,
                h.subgroup_id,
                h.driver_id,
                h.driver_name,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%FAIXA AMARELA%' THEN 1 END) AS faixa_amarela,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%FAIXA VERMELHA%' THEN 1 END) AS faixa_vermelha,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%BATENDO TRANSMISSÃO%' THEN 1 END) AS batendo_transmissao,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%PARADO ACELERANDO%' THEN 1 END) AS parado_acelerando,
                COUNT(CASE WHEN h.tracker_event_name ILIKE '%EXCESSO DE VELOCIDADE%' THEN 1 END) AS excesso_velocidade
            FROM
                mova.heatmap h
            WHERE
                h.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR h.subgroup_id = ANY(:subgroup_ids))
                AND (:driver_ids IS NULL OR h.driver_id = ANY(:driver_ids) OR h.driver_id IS NULL)
                AND (:unit_ids IS NULL OR h.unit_id = ANY(:unit_ids))
                AND h.local_time >= :start_date
                AND h.local_time <= :end_date
            GROUP BY
                DATE_TRUNC('hour', h.local_time),
                h.unit_id,
                h."label",
                h.label2,
                h.group_id,
                h.subgroup_id,
                h.driver_id,
                h.driver_name
            ORDER BY
                DATE_TRUNC('hour', h.local_time) DESC
        """).bindparams(
            bindparam("group_ids", type_=ARRAY(Integer)),
            bindparam("subgroup_ids", type_=ARRAY(Integer)),
            bindparam("driver_ids", type_=ARRAY(Integer)),
            bindparam("unit_ids", type_=ARRAY(Integer))
        )

        result = await db.execute(
            query,
            {
                "group_ids": accessible_groups_list,
                "subgroup_ids": subgroup_ids_list,
                "driver_ids": driver_ids_list,
                "unit_ids": unit_ids_list,
                "start_date": start_date,
                "end_date": end_date
            }
        )

        # Stream rows
        total_rows = 0
        chunk_size = 1000

        while True:
            rows = result.fetchmany(chunk_size)
            if not rows:
                break

            for row in rows:
                writer.writerow(row)
                total_rows += 1

            yield output.getvalue()
            output.truncate(0)
            output.seek(0)

        logger.info(
            "heatmap_csv_export_completed",
            total_rows=total_rows
        )

    filename = f"heatmap_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


# ============================================================================
# Weight Range (Metas e Pesos) Endpoints
# ============================================================================

@router.post("/weight-range/export/estimate", tags=["Reports/WeightRange"])
async def estimate_weight_range_export_size(
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Estimate export size and time for weight range report before starting export.

    **Returns:**
    - estimated_rows: Approximate number of rows
    - estimated_size_mb: Approximate file size in MB
    - estimated_time_seconds: Estimated time in seconds
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Get secure parameters (reusing existing function, but only need groups/subgroups)
    accessible_groups_list, subgroup_ids_list, _, _ = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            None,
            None
        )

    # Count with GROUP BY to match actual export result
    count_query = text("""
        SELECT COUNT(*) as total
        FROM (
            SELECT 1
            FROM mova.weight_range wr
            WHERE wr.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR wr.subgroup_id = ANY(:subgroup_ids))
            GROUP BY wr.range_id, wr.group_id, wr.subgroup_id
        ) AS aggregated_data
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer))
    )

    result = await db.execute(
        count_query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list
        }
    )

    row_count = result.scalar()

    # Estimate file size (avg 80 bytes per CSV row - smaller records)
    estimated_size_mb = (row_count * 80) / 1024 / 1024

    # Estimate time (~100k rows per second)
    estimated_time_seconds = max(row_count / 100000, 0.1)

    logger.info(
        "weight_range_export_estimation",
        estimated_rows=row_count,
        estimated_size_mb=round(estimated_size_mb, 2),
        estimated_time_seconds=round(estimated_time_seconds, 1)
    )

    return {
        "estimated_rows": row_count,
        "estimated_size_mb": round(estimated_size_mb, 2),
        "estimated_time_seconds": round(estimated_time_seconds, 1)
    }


@router.get("/weight-range/cursor", response_model=WeightRangeCursorResponse, tags=["Reports/WeightRange"])
async def get_weight_range_cursor(
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of records per page"),
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Get weight range (goals and weights) metrics from weight_range table.

    **Data Source:**
    - Table: mova.weight_range

    **Metrics Returned:**
    - range_id: Range identifier (1-6 for different metrics)
    - group_id: Group ID
    - subgroup_id: Subgroup ID
    - weight: Average weight for this range
    - goal: Average goal/target for this range

    **Pagination:**
    - Cursor-based pagination with row_id
    """
    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, _, _ = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            None,
            None
        )

    # Parse cursor
    cursor_row_id = None
    if cursor:
        try:
            decoded_cursor = WeightRangeCursor.from_string(cursor)
            cursor_row_id = decoded_cursor.row_id
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format."
            )

    # Build query
    query = text("""
        WITH ranked_data AS (
            SELECT
                wr.range_id,
                wr.group_id,
                wr.subgroup_id,
                AVG(wr.weight) AS weight,
                AVG(wr.goal) AS goal,
                ROW_NUMBER() OVER (ORDER BY wr.range_id, wr.group_id, wr.subgroup_id) AS row_id
            FROM
                mova.weight_range wr
            WHERE
                wr.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR wr.subgroup_id = ANY(:subgroup_ids))
            GROUP BY
                wr.range_id, wr.group_id, wr.subgroup_id
        )
        SELECT
            range_id, group_id, subgroup_id, weight, goal, row_id
        FROM ranked_data
        WHERE (
            :cursor_row_id IS NULL
            OR row_id > :cursor_row_id
        )
        ORDER BY range_id, group_id, subgroup_id
        LIMIT :limit
    """).bindparams(
        bindparam("group_ids", type_=ARRAY(Integer)),
        bindparam("subgroup_ids", type_=ARRAY(Integer)),
        bindparam("cursor_row_id", type_=BigInteger)
    )

    result = await db.execute(
        query,
        {
            "group_ids": accessible_groups_list,
            "subgroup_ids": subgroup_ids_list,
            "cursor_row_id": cursor_row_id,
            "limit": limit + 1
        }
    )

    rows = result.fetchall()

    # Check if there are more records
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Convert to response objects
    data = [
        WeightRangeResponse(
            range_id=row[0],
            group_id=row[1],
            subgroup_id=row[2],
            weight=float(row[3]) if row[3] is not None else 0.0,
            goal=float(row[4]) if row[4] is not None else 0.0
        )
        for row in rows
    ]

    # Create cursor for next page
    next_cursor = None
    if data and has_more:
        last_row_id = rows[limit - 1][5]
        cursor_obj = WeightRangeCursor(row_id=last_row_id)
        next_cursor = cursor_obj.to_string()

    return WeightRangeCursorResponse(
        data=data,
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(data)
    )


@router.get("/weight-range/export/csv", tags=["Reports/WeightRange"])
async def export_weight_range_csv(
    subgroup_ids: Optional[str] = Query(None, description="Comma-separated subgroup IDs"),
    db: AsyncSession = Depends(get_db_read),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    """
    Export weight range data as streaming CSV.

    **Data Source:**
    - Table: mova.weight_range

    **Features:**
    - Memory efficient streaming
    - Download starts immediately
    """
    from app.core.logging import get_logger
    logger = get_logger(__name__)

    # Get secure parameters
    accessible_groups_list, subgroup_ids_list, _, _ = \
        get_secure_driver_report_params(
            current_user.group_access,
            subgroup_ids,
            None,
            None
        )

    async def generate_csv():
        """Stream CSV with aggregated weight range data."""
        # CSV header
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Range ID", "Group ID", "Subgroup ID", "Weight", "Goal"
        ])
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        logger.info("weight_range_csv_export_started")

        # Query
        query = text("""
            SELECT
                wr.range_id,
                wr.group_id,
                wr.subgroup_id,
                AVG(wr.weight) AS weight,
                AVG(wr.goal) AS goal
            FROM
                mova.weight_range wr
            WHERE
                wr.group_id = ANY(:group_ids)
                AND (:subgroup_ids IS NULL OR wr.subgroup_id = ANY(:subgroup_ids))
            GROUP BY
                wr.range_id, wr.group_id, wr.subgroup_id
            ORDER BY
                wr.range_id, wr.group_id, wr.subgroup_id
        """).bindparams(
            bindparam("group_ids", type_=ARRAY(Integer)),
            bindparam("subgroup_ids", type_=ARRAY(Integer))
        )

        result = await db.execute(
            query,
            {
                "group_ids": accessible_groups_list,
                "subgroup_ids": subgroup_ids_list
            }
        )

        # Stream rows
        total_rows = 0
        chunk_size = 1000

        while True:
            rows = result.fetchmany(chunk_size)
            if not rows:
                break

            for row in rows:
                writer.writerow(row)
                total_rows += 1

            yield output.getvalue()
            output.truncate(0)
            output.seek(0)

        logger.info(
            "weight_range_csv_export_completed",
            total_rows=total_rows
        )

    filename = "weight_range.csv"

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )