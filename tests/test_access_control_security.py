"""
Security Tests for Access Control System.
Validates that group/subgroup filters prevent unauthorized data access.
"""

import pytest
from typing import List, Tuple, Optional

from app.core.query_filters import SQLAccessControlBuilder, validate_and_build_access_params
from app.core.secure_session import AccessControlHelper


class TestSubgroupAccessValidation:
    """
    Test suite for subgroup access validation.
    Ensures users cannot access subgroups they don't have permission for.
    """

    def test_user_with_specific_subgroups_can_request_only_accessible_subgroups(self):
        """
        Test: User with specific subgroup access can only request those subgroups.

        Scenario:
            User has access to: [(14330, 15812), (14330, 15813)]
            User requests: [15812, 15999]
            Expected: [15812] (15999 is rejected)
        """
        builder = SQLAccessControlBuilder()
        accessible_subgroups = [15812, 15813]
        requested_subgroups = [15812, 15999]

        result = builder.validate_requested_subgroups(
            requested_subgroups,
            accessible_subgroups
        )

        assert result == [15812], "Should only return subgroups user has access to"
        assert 15999 not in result, "Should reject unauthorized subgroup 15999"

    def test_user_with_null_subgroup_cannot_request_specific_subgroups(self):
        """
        Test: User with subgroup_id=NULL cannot request specific subgroups.

        Scenario:
            User has access to: [(14330, None)] (shared resources only)
            User requests: [15812, 15813]
            Expected: None (rejected - user has no specific subgroup access)

        Security:
            This prevents the critical vulnerability where users with NULL
            subgroup access could request data from specific subgroups.
        """
        builder = SQLAccessControlBuilder()
        accessible_subgroups = None  # User has no specific subgroup access
        requested_subgroups = [15812, 15813]

        result = builder.validate_requested_subgroups(
            requested_subgroups,
            accessible_subgroups
        )

        assert result is None, "User with NULL subgroup should not be able to request specific subgroups"

    def test_user_requesting_empty_list_returns_none(self):
        """Test: Requesting empty list of subgroups returns None."""
        builder = SQLAccessControlBuilder()
        accessible_subgroups = [15812, 15813]
        requested_subgroups = []

        result = builder.validate_requested_subgroups(
            requested_subgroups,
            accessible_subgroups
        )

        assert result is None, "Empty request should return None"

    def test_user_requesting_all_unauthorized_subgroups_returns_none(self):
        """Test: Requesting only unauthorized subgroups returns None."""
        builder = SQLAccessControlBuilder()
        accessible_subgroups = [15812, 15813]
        requested_subgroups = [15999, 16000]  # All unauthorized

        result = builder.validate_requested_subgroups(
            requested_subgroups,
            accessible_subgroups
        )

        assert result is None, "All unauthorized requests should return None"


class TestAccessParamsBuilder:
    """
    Test suite for validate_and_build_access_params function.
    Tests end-to-end parameter validation for driver reports.
    """

    def test_extract_groups_and_subgroups_from_user_access(self):
        """
        Test: Correctly extracts unique group_ids and subgroup_ids.

        Scenario:
            User access: [(14330, 15812), (14330, 15813), (14331, None)]
            Expected groups: [14330, 14331]
            Expected subgroups: [15812, 15813]
        """
        user_access = [(14330, 15812), (14330, 15813), (14331, None)]
        builder = SQLAccessControlBuilder()

        groups, subgroups = builder.extract_accessible_groups_and_subgroups(user_access)

        assert set(groups) == {14330, 14331}, "Should extract unique group_ids"
        assert set(subgroups) == {15812, 15813}, "Should extract non-NULL subgroup_ids"

    def test_validate_and_build_with_valid_subgroups(self):
        """
        Test: validate_and_build_access_params with valid subgroup request.

        Scenario:
            User access: [(14330, 15812), (14330, 15813)]
            Requested: "15812,15813"
            Expected: groups=[14330], subgroups=[15812, 15813]
        """
        user_access = [(14330, 15812), (14330, 15813)]
        requested_subgroups = "15812,15813"

        groups, subgroups = validate_and_build_access_params(
            user_access,
            requested_subgroups
        )

        assert groups == [14330]
        assert set(subgroups) == {15812, 15813}

    def test_validate_and_build_with_invalid_subgroups(self):
        """
        Test: validate_and_build_access_params rejects unauthorized subgroups.

        Scenario:
            User access: [(14330, 15812)]
            Requested: "15812,15999"  # 15999 is unauthorized
            Expected: groups=[14330], subgroups=[15812]  # 15999 rejected
        """
        user_access = [(14330, 15812)]
        requested_subgroups = "15812,15999"

        groups, subgroups = validate_and_build_access_params(
            user_access,
            requested_subgroups
        )

        assert groups == [14330]
        assert subgroups == [15812], "Should only include authorized subgroup"
        assert 15999 not in subgroups, "Unauthorized subgroup should be rejected"

    def test_validate_and_build_with_null_subgroup_access(self):
        """
        Test: User with NULL subgroup cannot filter by specific subgroups.

        Scenario:
            User access: [(14330, None)]  # Access to shared resources only
            Requested: "15812"
            Expected: groups=[14330], subgroups=None  # Request rejected
        """
        user_access = [(14330, None)]
        requested_subgroups = "15812"

        groups, subgroups = validate_and_build_access_params(
            user_access,
            requested_subgroups
        )

        assert groups == [14330]
        assert subgroups is None, "User with NULL subgroup should not get specific subgroups"

    def test_validate_and_build_with_invalid_format_raises_error(self):
        """Test: Invalid subgroup_ids format raises ValueError."""
        user_access = [(14330, 15812)]
        invalid_subgroups = "15812,abc,15813"  # "abc" is invalid

        with pytest.raises(ValueError, match="Invalid subgroup_ids format"):
            validate_and_build_access_params(user_access, invalid_subgroups)


class TestAccessControlHelper:
    """
    Test suite for AccessControlHelper.
    Tests resource access validation logic.
    """

    def test_user_can_access_resource_in_same_subgroup(self):
        """Test: User can access resource from their subgroup."""
        user_access = [(14330, 15812)]
        resource_group_id = 14330
        resource_subgroup_id = 15812

        has_access = AccessControlHelper.has_access_to_resource(
            resource_group_id,
            resource_subgroup_id,
            user_access
        )

        assert has_access is True, "User should access resource from their subgroup"

    def test_user_can_access_shared_resource_null_subgroup(self):
        """Test: User can access shared resources (subgroup_id=NULL)."""
        user_access = [(14330, 15812)]
        resource_group_id = 14330
        resource_subgroup_id = None  # Shared resource

        has_access = AccessControlHelper.has_access_to_resource(
            resource_group_id,
            resource_subgroup_id,
            user_access
        )

        assert has_access is True, "User should access shared resources in their group"

    def test_user_cannot_access_resource_from_other_subgroup(self):
        """Test: User cannot access resource from different subgroup."""
        user_access = [(14330, 15812)]
        resource_group_id = 14330
        resource_subgroup_id = 15999  # Different subgroup

        has_access = AccessControlHelper.has_access_to_resource(
            resource_group_id,
            resource_subgroup_id,
            user_access
        )

        assert has_access is False, "User should not access other subgroups"

    def test_user_cannot_access_resource_from_other_group(self):
        """Test: User cannot access resource from different group."""
        user_access = [(14330, 15812)]
        resource_group_id = 14331  # Different group
        resource_subgroup_id = 15812

        has_access = AccessControlHelper.has_access_to_resource(
            resource_group_id,
            resource_subgroup_id,
            user_access
        )

        assert has_access is False, "User should not access other groups"

    def test_user_with_null_subgroup_can_only_access_shared_resources(self):
        """Test: User with NULL subgroup can only access shared resources."""
        user_access = [(14330, None)]  # NULL = shared resources only

        # Can access shared resource
        assert AccessControlHelper.has_access_to_resource(
            14330, None, user_access
        ) is True, "Should access shared resource"

        # Cannot access specific subgroup
        assert AccessControlHelper.has_access_to_resource(
            14330, 15812, user_access
        ) is False, "Should NOT access specific subgroup"

    def test_user_with_multiple_subgroups_can_access_any_of_them(self):
        """Test: User with multiple subgroups can access any of them."""
        user_access = [(14330, 15812), (14330, 15813), (14331, 99999)]

        # Can access first subgroup
        assert AccessControlHelper.has_access_to_resource(
            14330, 15812, user_access
        ) is True

        # Can access second subgroup
        assert AccessControlHelper.has_access_to_resource(
            14330, 15813, user_access
        ) is True

        # Can access subgroup in different group
        assert AccessControlHelper.has_access_to_resource(
            14331, 99999, user_access
        ) is True

        # Cannot access unauthorized subgroup
        assert AccessControlHelper.has_access_to_resource(
            14330, 15999, user_access
        ) is False


class TestSQLFilterBuilder:
    """
    Test suite for SQL filter string generation.
    """

    def test_build_group_subgroup_sql_filter(self):
        """Test: Correct SQL WHERE clause is generated."""
        sql_filter = SQLAccessControlBuilder.build_group_subgroup_sql_filter(
            table_alias="hk",
            group_param="group_ids",
            subgroup_param="subgroup_ids"
        )

        assert "hk.group_id = ANY(:group_ids)" in sql_filter
        assert "(:subgroup_ids IS NULL OR hk.subgroup_id = ANY(:subgroup_ids))" in sql_filter

    def test_get_sql_bind_params(self):
        """Test: Correct bind parameters are returned."""
        params = SQLAccessControlBuilder.get_sql_bind_params()

        assert "group_ids" in params
        assert "subgroup_ids" in params


# Integration test scenarios
class TestIntegrationScenarios:
    """
    Integration tests simulating real attack scenarios.
    """

    def test_scenario_attack_user_tries_to_access_other_subgroup(self):
        """
        ATTACK SCENARIO: User with subgroup A tries to request data from subgroup B.

        Setup:
            Attacker has: [(14330, 15812)]  # Subgroup A
            Attacker requests: "15813"      # Subgroup B (unauthorized)

        Expected:
            Request is rejected - no data from subgroup 15813
        """
        attacker_access = [(14330, 15812)]
        malicious_request = "15813"  # Trying to access other subgroup

        groups, subgroups = validate_and_build_access_params(
            attacker_access,
            malicious_request
        )

        # Attack is blocked
        assert subgroups is None or 15813 not in subgroups, \
            "SECURITY BREACH: Attacker gained access to unauthorized subgroup!"

    def test_scenario_attack_null_subgroup_user_requests_specific_subgroups(self):
        """
        ATTACK SCENARIO: User with NULL subgroup tries to access specific subgroups.

        This was the ACTUAL VULNERABILITY found in the original code.

        Setup:
            Attacker has: [(14330, None)]       # Should only see shared resources
            Attacker requests: "15812,15813"    # Trying to access specific subgroups

        Expected:
            Request is rejected - no subgroup filtering allowed
        """
        attacker_access = [(14330, None)]
        malicious_request = "15812,15813"

        groups, subgroups = validate_and_build_access_params(
            attacker_access,
            malicious_request
        )

        # Attack is blocked
        assert subgroups is None, \
            "CRITICAL VULNERABILITY: User with NULL subgroup accessed specific subgroups!"

    def test_scenario_legitimate_user_with_multiple_subgroups(self):
        """
        LEGITIMATE SCENARIO: User with multiple subgroups requests valid data.

        Setup:
            User has: [(14330, 15812), (14330, 15813)]
            User requests: "15812"  # Valid request

        Expected:
            Request is allowed
        """
        user_access = [(14330, 15812), (14330, 15813)]
        valid_request = "15812"

        groups, subgroups = validate_and_build_access_params(
            user_access,
            valid_request
        )

        assert subgroups == [15812], "Legitimate request should be allowed"
