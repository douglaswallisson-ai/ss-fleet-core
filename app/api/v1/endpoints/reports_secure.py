"""
Secure helper functions for reports endpoints.
Provides validated access control for driver reports.
"""

from typing import Optional, Tuple, List
from fastapi import HTTPException, status

from app.core.query_filters import validate_and_build_access_params
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_secure_driver_report_params(
    user_group_access: List[Tuple[int, Optional[int]]],
    subgroup_ids: Optional[str] = None,
    driver_ids: Optional[str] = None,
    unit_ids: Optional[str] = None
) -> Tuple[List[int], Optional[List[int]], Optional[List[int]], Optional[List[int]]]:
    """
    Build secure parameters for driver reports with validated access control.

    This function prevents the security vulnerability where users could request
    data from subgroups they don't have access to.

    Args:
        user_group_access: User's group access from AuthenticatedUser.group_access
        subgroup_ids: Comma-separated subgroup IDs (optional, will be validated)
        driver_ids: Comma-separated driver IDs (optional)
        unit_ids: Comma-separated unit/vehicle IDs (optional)

    Returns:
        Tuple of (group_ids, subgroup_ids, driver_ids, unit_ids) for SQL query

    Raises:
        HTTPException: If user has no group access

    Security:
        - Validates requested subgroups against user's accessible subgroups
        - Prevents users with subgroup_id=NULL from accessing specific subgroups
        - Only allows filtering by subgroups the user has explicit access to
    """
    # Check user has group access
    if not user_group_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No group access"
        )

    # ✅ SECURITY FIX: Use validated access params builder
    # This prevents users from requesting subgroups they don't have access to
    accessible_groups_list, subgroup_ids_list = validate_and_build_access_params(
        user_group_access,
        subgroup_ids
    )

    # Parse driver IDs (no security validation needed - just formatting)
    driver_ids_list = None
    if driver_ids:
        try:
            driver_ids_list = [int(did.strip()) for did in driver_ids.split(",")]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid driver_ids format: {driver_ids}"
            )

    # Parse unit IDs (no security validation needed - just formatting)
    unit_ids_list = None
    if unit_ids:
        try:
            unit_ids_list = [int(uid.strip()) for uid in unit_ids.split(",")]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid unit_ids format: {unit_ids}"
            )

    logger.info(
        "driver_report_params_validated",
        group_count=len(accessible_groups_list),
        subgroup_count=len(subgroup_ids_list) if subgroup_ids_list else 0,
        driver_count=len(driver_ids_list) if driver_ids_list else 0,
        unit_count=len(unit_ids_list) if unit_ids_list else 0
    )

    return accessible_groups_list, subgroup_ids_list, driver_ids_list, unit_ids_list
