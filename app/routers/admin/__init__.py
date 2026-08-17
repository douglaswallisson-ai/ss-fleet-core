"""Admin routers package"""

from app.routers.admin.permissions import router as permissions_router
from app.routers.admin.user_group_access import router as user_group_access_router

__all__ = ["permissions_router", "user_group_access_router"]
