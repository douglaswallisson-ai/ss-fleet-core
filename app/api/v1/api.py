"""
API v1 router aggregation.
Combines all endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, vehicles, devices, tracked_unit_devices, vcms_unit_devices, drivers, reports, groups, subgroups, history_detailed
from app.routers.admin import permissions_router, user_group_access_router

api_router = APIRouter()

# Include endpoint routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(groups.router, prefix="/groups", tags=["Groups"])
api_router.include_router(subgroups.router, prefix="/subgroups", tags=["Subgroups"])
api_router.include_router(vehicles.router, prefix="/vehicles", tags=["Vehicles"])
api_router.include_router(devices.router, prefix="/devices", tags=["Devices"])
api_router.include_router(drivers.router, prefix="/drivers", tags=["Drivers"])
api_router.include_router(tracked_unit_devices.router, prefix="/device-associations", tags=["Device Associations"])
api_router.include_router(vcms_unit_devices.router, prefix="/video-device-associations", tags=["Video Device Associations"])
api_router.include_router(reports.router, prefix="/reports")
api_router.include_router(history_detailed.router, prefix="/reports")

# Admin routers
api_router.include_router(permissions_router)
api_router.include_router(user_group_access_router)
