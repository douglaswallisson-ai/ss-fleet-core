"""
Query Filter Utilities.
Provides helpers to build SQL WHERE clauses for access control in raw SQL queries.
"""

from typing import List, Tuple, Optional, Dict
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import Integer, bindparam

from app.core.logging import get_logger

logger = get_logger(__name__)


class SQLAccessControlBuilder:
    """
    Builder for SQL WHERE clauses that enforce group/subgroup access control.
    Used for raw SQL queries where ORM filters cannot be applied.
    """

    @staticmethod
    def extract_accessible_groups_and_subgroups(
        group_access: List[Tuple[int, Optional[int]]]
    ) -> Tuple[List[int], Optional[List[int]]]:
        """
        Extract unique group_ids and subgroup_ids from user's access list.

        Args:
            group_access: List of (group_id, subgroup_id) tuples

        Returns:
            Tuple of (list of group_ids, list of subgroup_ids or None)

        Example:
            Input: [(14330, 15812), (14330, 15813), (14331, None)]
            Output: ([14330, 14331], [15812, 15813])
        """
        accessible_groups = set()
        accessible_subgroups = set()

        for group_id, subgroup_id in group_access:
            accessible_groups.add(group_id)
            if subgroup_id is not None:
                accessible_subgroups.add(subgroup_id)

        groups_list = list(accessible_groups)
        subgroups_list = list(accessible_subgroups) if accessible_subgroups else None

        return groups_list, subgroups_list

    @staticmethod
    def validate_requested_subgroups(
        requested_subgroups: List[int],
        accessible_subgroups: Optional[List[int]]
    ) -> Optional[List[int]]:
        """
        Validate and filter requested subgroup_ids against user's accessible subgroups.

        This prevents the vulnerability where users could request subgroups
        they don't have access to.

        Args:
            requested_subgroups: Subgroup IDs requested by user (from query params)
            accessible_subgroups: Subgroup IDs user has access to (from group_access)

        Returns:
            Filtered list of valid subgroup IDs; [-1] (matches nothing) when none
            of the requested subgroups is allowed; None only when the user has
            group-wide access

        Security:
            - If user has NO specific subgroup access (accessible_subgroups is None),
              they should NOT be able to filter by subgroups (returns None)
            - If user has specific subgroup access, only return intersection

        Example:
            User access: [(14330, 15812), (14330, 15813)]
            Requested: [15812, 15999]
            Result: [15812]  (15999 is filtered out)

            User access: [(14330, None)]  (access to shared resources only)
            Requested: [15812, 15813]
            Result: None  (user cannot request specific subgroups)
        """
        if accessible_subgroups is None:
            # User has no specific subgroup access (subgroup_id = NULL in permissions)
            # They can only see shared resources, so DO NOT allow filtering by subgroups
            logger.warning(
                "subgroup_filter_rejected",
                reason="User has no specific subgroup access",
                requested=requested_subgroups
            )
            return None

        # User has specific subgroup access - return intersection
        valid_subgroups = [
            sg for sg in requested_subgroups
            if sg in accessible_subgroups
        ]

        if len(valid_subgroups) < len(requested_subgroups):
            rejected = set(requested_subgroups) - set(valid_subgroups)
            logger.warning(
                "subgroup_filter_partial_rejection",
                requested=requested_subgroups,
                valid=valid_subgroups,
                rejected=list(rejected)
            )

        # Nenhum pedido válido = nenhum subgrupo, nunca "sem filtro". Devolver
        # None aqui virava `:subgroup_ids IS NULL` no SQL e liberava o grupo
        # inteiro para quem pediu um subgrupo proibido (vault:
        # multi-tenant-account-id, 3.1 — o mesmo tipo de falha do SEC-2025-001).
        # -1 não existe como subgrupo, então o filtro não casa com nada.
        return valid_subgroups if valid_subgroups else [-1]

    @staticmethod
    def build_group_subgroup_sql_filter(
        table_alias: str = "hk",
        group_param: str = "group_ids",
        subgroup_param: str = "subgroup_ids"
    ) -> str:
        """
        Build SQL WHERE clause for group/subgroup filtering.

        Args:
            table_alias: Alias of the table being queried (default: "hk")
            group_param: Name of the bind parameter for group_ids
            subgroup_param: Name of the bind parameter for subgroup_ids

        Returns:
            SQL WHERE clause string

        Example:
            SQL fragment:
            WHERE hk.group_id = ANY(:group_ids)
              AND (:subgroup_ids IS NULL OR hk.subgroup_id = ANY(:subgroup_ids))

        Usage:
            query = text(f'''
                SELECT * FROM mova.con_driver_h_km hk
                {SQLAccessControlBuilder.build_group_subgroup_sql_filter("hk")}
            ''')
        """
        return f"""
            {table_alias}.group_id = ANY(:{group_param})
            AND (:{subgroup_param} IS NULL OR {table_alias}.subgroup_id = ANY(:{subgroup_param}))
        """.strip()

    @staticmethod
    def get_sql_bind_params() -> Dict:
        """
        Get SQLAlchemy bindparam definitions for access control parameters.

        Returns:
            Dictionary of bindparam definitions

        Usage:
            query = text('''
                SELECT * FROM table
                WHERE group_id = ANY(:group_ids)
                  AND (:subgroup_ids IS NULL OR subgroup_id = ANY(:subgroup_ids))
            ''').bindparams(**SQLAccessControlBuilder.get_sql_bind_params())
        """
        return {
            "group_ids": bindparam("group_ids", type_=ARRAY(Integer)),
            "subgroup_ids": bindparam("subgroup_ids", type_=ARRAY(Integer)),
        }


def validate_and_build_access_params(
    user_group_access: List[Tuple[int, Optional[int]]],
    requested_subgroup_ids: Optional[str] = None
) -> Tuple[List[int], Optional[List[int]]]:
    """
    High-level helper to validate user's request and build SQL parameters.

    Args:
        user_group_access: User's group access from AuthenticatedUser.group_access
        requested_subgroup_ids: Comma-separated subgroup IDs from query params (optional)

    Returns:
        Tuple of (group_ids, subgroup_ids) ready for SQL query

    Raises:
        ValueError: If requested subgroups are invalid

    Example:
        # In endpoint:
        group_ids, subgroup_ids = validate_and_build_access_params(
            current_user.group_access,
            request.query_params.get('subgroup_ids')
        )

        result = await db.execute(
            query,
            {"group_ids": group_ids, "subgroup_ids": subgroup_ids}
        )
    """
    builder = SQLAccessControlBuilder()

    # Extract accessible groups and subgroups
    accessible_groups, accessible_subgroups = \
        builder.extract_accessible_groups_and_subgroups(user_group_access)

    # Parse requested subgroups if provided
    subgroup_ids_list = None
    if requested_subgroup_ids:
        try:
            requested = [int(sid.strip()) for sid in requested_subgroup_ids.split(",")]
            # Validate against accessible subgroups
            subgroup_ids_list = builder.validate_requested_subgroups(
                requested,
                accessible_subgroups
            )
        except ValueError as e:
            logger.error("invalid_subgroup_ids", error=str(e), value=requested_subgroup_ids)
            raise ValueError(f"Invalid subgroup_ids format: {requested_subgroup_ids}")
    elif accessible_subgroups:
        # User has specific subgroup access but didn't request filtering
        # Use their accessible subgroups
        subgroup_ids_list = accessible_subgroups

    logger.debug(
        "access_params_built",
        group_ids=accessible_groups,
        subgroup_ids=subgroup_ids_list,
        requested_subgroups=requested_subgroup_ids
    )

    return accessible_groups, subgroup_ids_list
