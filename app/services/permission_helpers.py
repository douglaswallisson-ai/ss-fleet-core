"""
Permission Helper Functions

Utilities for permission checking and HTTP method mapping.
"""

from typing import Optional, Tuple


def http_method_to_action(method: str) -> str:
    """
    Map HTTP method to action name.

    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE)

    Returns:
        Action name (read, create, update, delete)

    Examples:
        >>> http_method_to_action("GET")
        'read'
        >>> http_method_to_action("POST")
        'create'
        >>> http_method_to_action("PUT")
        'update'
    """
    method = method.upper()

    mapping = {
        "GET": "read",
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }

    return mapping.get(method, "read")


def extract_resource_from_path(path: str) -> Optional[str]:
    """
    Extract resource name from API path.

    Args:
        path: API path (e.g., "/api/v1/vehicles/123")

    Returns:
        Resource name or None if not found

    Examples:
        >>> extract_resource_from_path("/api/v1/vehicles/123")
        'vehicles'
        >>> extract_resource_from_path("/api/v1/devices")
        'devices'
        >>> extract_resource_from_path("/api/v1/drivers/456/assign")
        'drivers'
    """
    # Remove query parameters
    path = path.split("?")[0]

    # Split path into parts
    parts = [p for p in path.split("/") if p]

    # Known resource names (from api_resources table)
    known_resources = {
        "vehicles",
        "drivers",
        "devices",
        "reports",
        "users",
        "groups",
    }

    # Find first matching resource in path
    for part in parts:
        if part in known_resources:
            return part

    return None


def build_permission_key(resource: str, action: str) -> str:
    """
    Build permission key from resource and action.

    Args:
        resource: Resource name (e.g., "vehicles")
        action: Action name (e.g., "read")

    Returns:
        Permission key (e.g., "vehicles.read")

    Examples:
        >>> build_permission_key("vehicles", "read")
        'vehicles.read'
        >>> build_permission_key("devices", "create")
        'devices.create'
    """
    return f"{resource}.{action}"


def get_permission_from_request(method: str, path: str) -> Optional[str]:
    """
    Extract required permission from HTTP request.

    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE)
        path: API path (e.g., "/api/v1/vehicles/123")

    Returns:
        Permission key (e.g., "vehicles.read") or None if not a resource endpoint

    Examples:
        >>> get_permission_from_request("GET", "/api/v1/vehicles/123")
        'vehicles.read'
        >>> get_permission_from_request("POST", "/api/v1/devices")
        'devices.create'
        >>> get_permission_from_request("DELETE", "/api/v1/drivers/456")
        'drivers.delete'
    """
    resource = extract_resource_from_path(path)
    if not resource:
        return None

    action = http_method_to_action(method)
    return build_permission_key(resource, action)


def parse_permission_key(permission_key: str) -> Tuple[str, str]:
    """
    Parse permission key into resource and action.

    Args:
        permission_key: Permission key (e.g., "vehicles.read")

    Returns:
        Tuple of (resource, action)

    Examples:
        >>> parse_permission_key("vehicles.read")
        ('vehicles', 'read')
        >>> parse_permission_key("devices.create")
        ('devices', 'create')
    """
    parts = permission_key.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", ""
