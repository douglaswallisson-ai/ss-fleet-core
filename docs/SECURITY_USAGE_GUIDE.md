# Security Usage Guide - Access Control System

Quick reference for developers on how to use the new access control system.

---

## For API Endpoint Development

### ✅ Creating Secure Driver Report Endpoints

**Always use the secure helper function** for driver reports:

```python
from app.api.v1.endpoints.reports_secure import get_secure_driver_report_params

@router.get("/my-driver-report")
async def my_driver_report(
    subgroup_ids: Optional[str] = Query(None),
    driver_ids: Optional[str] = Query(None),
    unit_ids: Optional[str] = Query(None),
    current_user: AuthenticatedUser = Depends(require_permission("reports", "read"))
):
    # ✅ SECURE: Validates subgroups against user's permissions
    groups, subgroups, drivers, units = get_secure_driver_report_params(
        current_user.group_access,
        subgroup_ids,
        driver_ids,
        unit_ids
    )

    # Use validated parameters in your query
    query = text("""
        SELECT * FROM mova.con_driver_h_km
        WHERE group_id = ANY(:group_ids)
          AND (:subgroup_ids IS NULL OR subgroup_id = ANY(:subgroup_ids))
    """)

    result = await db.execute(query, {
        "group_ids": groups,
        "subgroup_ids": subgroups
    })
```

### ✅ Creating Secure Vehicle/ORM Endpoints

**Use build_group_subgroup_filter()** for ORM queries:

```python
from app.core.access_control import build_group_subgroup_filter

@router.get("/vehicles")
async def list_vehicles(
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read")),
    db: AsyncSession = Depends(get_db)
):
    # Check user has access
    if not current_user.group_access:
        raise HTTPException(403, "No group access")

    # ✅ SECURE: Build automatic filter
    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)

    # Execute query with filter
    result = await db.execute(
        select(Vehicle).where(access_filter)
    )

    return result.scalars().all()
```

---

## For Testing

### Running Security Tests

```bash
# All security tests
pytest tests/test_access_control_security.py -v

# Specific test class
pytest tests/test_access_control_security.py::TestSubgroupAccessValidation -v

# With coverage
pytest tests/test_access_control_security.py --cov=app --cov-report=html
```

### Writing Security Tests

```python
def test_my_endpoint_rejects_unauthorized_subgroups():
    """Test: Endpoint rejects subgroups user doesn't have access to."""
    # Setup user with limited access
    user_access = [(14330, 15812)]  # Only subgroup 15812

    # Try to access unauthorized subgroup
    groups, subgroups = validate_and_build_access_params(
        user_access,
        requested_subgroup_ids="15999"  # Unauthorized
    )

    # Verify request is rejected
    assert 15999 not in (subgroups or []), "Should reject unauthorized subgroup"
```

---

## Common Patterns

### Pattern 1: Validating Manual Input

```python
from app.core.query_filters import validate_and_build_access_params

# User provides: ?subgroup_ids=15812,15813
groups, subgroups = validate_and_build_access_params(
    current_user.group_access,
    request_params.subgroup_ids
)

# Result: Only returns subgroups user has access to
```

### Pattern 2: Checking Single Resource Access

```python
from app.core.secure_session import AccessControlHelper

# Check if user can access a specific vehicle
has_access = AccessControlHelper.has_access_to_resource(
    vehicle.group_id,
    vehicle.subgroup_id,
    current_user.group_access
)

if not has_access:
    raise HTTPException(404, "Vehicle not found")  # Don't reveal it exists
```

### Pattern 3: Building SQL WHERE Clauses

```python
from app.core.query_filters import SQLAccessControlBuilder

# Get SQL filter string
sql_filter = SQLAccessControlBuilder.build_group_subgroup_sql_filter(
    table_alias="hk",
    group_param="group_ids",
    subgroup_param="subgroup_ids"
)

# Use in raw SQL
query = text(f"""
    SELECT * FROM mova.my_table hk
    WHERE {sql_filter}
      AND hk.some_other_condition = :value
""")
```

---

## ❌ Common Mistakes to Avoid

### Mistake 1: Trusting User Input Directly

```python
# ❌ WRONG - Never do this!
if subgroup_ids:
    subgroup_list = [int(x.strip()) for x in subgroup_ids.split(",")]
    # Using subgroup_list without validation!

# ✅ CORRECT - Always validate
groups, subgroups = validate_and_build_access_params(
    current_user.group_access,
    subgroup_ids
)
```

### Mistake 2: Allowing NULL Subgroup Users to Filter

```python
# ❌ WRONG - Vulnerable!
if accessible_subgroups_list:
    filtered = [sg for sg in requested if sg in accessible_subgroups_list]
else:
    filtered = requested  # ❌ Accepts anything!

# ✅ CORRECT - Reject if user has no specific access
builder = SQLAccessControlBuilder()
filtered = builder.validate_requested_subgroups(requested, accessible_subgroups_list)
# Returns None if user has no specific access
```

### Mistake 3: Forgetting to Check Group Access

```python
# ❌ WRONG - No access check!
@router.get("/data")
async def get_data(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Vehicle))  # ❌ No filtering!

# ✅ CORRECT - Always check and filter
@router.get("/data")
async def get_data(
    current_user: AuthenticatedUser = Depends(require_permission("vehicles", "read")),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.group_access:
        raise HTTPException(403, "No group access")

    access_filter = build_group_subgroup_filter(current_user.group_access)(Vehicle)
    result = await db.execute(select(Vehicle).where(access_filter))
```

---

## Quick Reference

### Key Functions

| Function | Use Case |
|----------|----------|
| `get_secure_driver_report_params()` | Driver reports with subgroup validation |
| `validate_and_build_access_params()` | Manual parameter validation |
| `build_group_subgroup_filter()` | ORM query filtering |
| `AccessControlHelper.has_access_to_resource()` | Single resource check |
| `SQLAccessControlBuilder.build_group_subgroup_sql_filter()` | Raw SQL WHERE clause |

### Key Files

| File | Purpose |
|------|---------|
| `app/api/v1/endpoints/reports_secure.py` | Secure helper for driver reports |
| `app/core/query_filters.py` | SQL validation utilities |
| `app/core/access_control.py` | Core filter building logic |
| `app/core/secure_session.py` | Context management |
| `tests/test_access_control_security.py` | Security test examples |

---

## Need Help?

1. **Examples**: Check existing endpoints in `app/api/v1/endpoints/reports.py`
2. **Tests**: See `tests/test_access_control_security.py` for test patterns
3. **Documentation**: Read `SECURITY.md` for detailed architecture
4. **Troubleshooting**: Check logs for "access_control" events

---

**Last Updated**: 2025-11-16
