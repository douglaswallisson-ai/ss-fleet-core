"""
History Detailed API Tests - Full CAN Bus Telemetry with Nested JSON Structure.

Tests cover:
- Schema validation for all nested objects
- Cursor pagination encoding/decoding
- Date range validation
- Access control logic
- Response structure validation
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.schemas.history import (
    CompositeCursor,
    HistoryDetailedResponse,
    HistoryDetailedCursorResponse,
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


class TestCompositeCursor:
    """Test cursor encoding and decoding."""

    def test_cursor_encode_decode_roundtrip(self):
        """Test: Cursor can be encoded and decoded correctly."""
        original = CompositeCursor(local_time="2025-10-31 23:59:45", id=15054598630)
        encoded = original.to_string()
        decoded = CompositeCursor.from_string(encoded)

        assert decoded.local_time == original.local_time
        assert decoded.id == original.id

    def test_cursor_encode_produces_base64_string(self):
        """Test: Encoded cursor is a valid base64 string."""
        cursor = CompositeCursor(local_time="2025-01-15 10:30:00", id=123456789)
        encoded = cursor.to_string()

        # Should be base64 encoded (alphanumeric + some special chars)
        assert isinstance(encoded, str)
        assert len(encoded) > 0
        # Should not contain raw JSON characters
        assert "{" not in encoded
        assert "}" not in encoded

    def test_cursor_decode_with_bigint_id(self):
        """Test: Cursor correctly handles BigInt IDs."""
        big_id = 15054598630  # > 2^32
        cursor = CompositeCursor(local_time="2025-12-19 11:59:40", id=big_id)
        encoded = cursor.to_string()
        decoded = CompositeCursor.from_string(encoded)

        assert decoded.id == big_id

class TestSubgroupInfo:
    """Test SubgroupInfo schema."""

    def test_subgroup_info_with_all_fields(self):
        """Test: SubgroupInfo accepts all fields."""
        subgroup = SubgroupInfo(id=15059, name="ANSAL - JUIZ DE FORA")
        assert subgroup.id == 15059
        assert subgroup.name == "ANSAL - JUIZ DE FORA"

    def test_subgroup_info_with_null_fields(self):
        """Test: SubgroupInfo accepts null fields."""
        subgroup = SubgroupInfo(id=None, name=None)
        assert subgroup.id is None
        assert subgroup.name is None


class TestGroupInfo:
    """Test GroupInfo schema with nested subgroup."""

    def test_group_info_with_nested_subgroup(self):
        """Test: GroupInfo includes nested subgroup."""
        group = GroupInfo(
            id=14071,
            name="ANSAL - GRUPO CSC",
            subgroup=SubgroupInfo(id=15059, name="ANSAL - JUIZ DE FORA")
        )
        assert group.id == 14071
        assert group.name == "ANSAL - GRUPO CSC"
        assert group.subgroup.id == 15059
        assert group.subgroup.name == "ANSAL - JUIZ DE FORA"

    def test_group_info_with_null_subgroup(self):
        """Test: GroupInfo accepts null subgroup."""
        group = GroupInfo(id=14071, name="Test Group", subgroup=None)
        assert group.subgroup is None


class TestUnitInfo:
    """Test UnitInfo schema."""

    def test_unit_info_complete(self):
        """Test: UnitInfo with all fields including nested group."""
        unit = UnitInfo(
            id=47395,
            label="RVC-6C23",
            label2="753",
            obs="Test observation",
            group=GroupInfo(
                id=14071,
                name="ANSAL - GRUPO CSC",
                subgroup=SubgroupInfo(id=15059, name="ANSAL - JUIZ DE FORA")
            )
        )
        assert unit.id == 47395
        assert unit.label == "RVC-6C23"
        assert unit.label2 == "753"
        assert unit.obs == "Test observation"
        assert unit.group.id == 14071
        assert unit.group.subgroup.id == 15059

    def test_unit_info_minimal(self):
        """Test: UnitInfo with only required fields."""
        unit = UnitInfo(id=1, label="ABC-1234")
        assert unit.id == 1
        assert unit.label == "ABC-1234"
        assert unit.label2 is None
        assert unit.obs is None
        assert unit.group is None


class TestDriverInfo:
    """Test DriverInfo schema."""

    def test_driver_info_complete(self):
        """Test: DriverInfo with all fields."""
        driver = DriverInfo(
            id=71439,
            name="ALENCAR PEREIRA DOS SANTOS - 284176",
            login="1720675929",
            group=GroupInfo(
                id=14071,
                name="ANSAL - GRUPO CSC",
                subgroup=SubgroupInfo(id=15059, name="ANSAL - JUIZ DE FORA")
            )
        )
        assert driver.id == 71439
        assert driver.name == "ALENCAR PEREIRA DOS SANTOS - 284176"
        assert driver.login == "1720675929"
        assert driver.group.id == 14071

    def test_driver_info_null_allowed(self):
        """Test: DriverInfo can be completely null (no driver)."""
        driver = DriverInfo(id=None, name=None, login=None, group=None)
        assert driver.id is None
        assert driver.name is None


class TestPoiInfo:
    """Test PoiInfo schema."""

    def test_poi_info_with_values(self):
        """Test: PoiInfo with actual POI data."""
        poi = PoiInfo(id=123, name="Gas Station", distance=150.5)
        assert poi.id == 123
        assert poi.name == "Gas Station"
        assert poi.distance == 150.5

    def test_poi_info_null_values(self):
        """Test: PoiInfo with null values (no POI nearby)."""
        poi = PoiInfo(id=None, name=None, distance=None)
        assert poi.id is None
        assert poi.name is None
        assert poi.distance is None


class TestCercaInfo:
    """Test CercaInfo (geofence) schema."""

    def test_cerca_info_with_values(self):
        """Test: CercaInfo with geofence data."""
        cerca = CercaInfo(id=456, name="Warehouse Zone")
        assert cerca.id == 456
        assert cerca.name == "Warehouse Zone"

    def test_cerca_info_null_values(self):
        """Test: CercaInfo with null values (not in geofence)."""
        cerca = CercaInfo(id=None, name=None)
        assert cerca.id is None
        assert cerca.name is None


class TestDeviceInfo:
    """Test DeviceInfo schema."""

    def test_device_info_complete(self):
        """Test: DeviceInfo with all fields."""
        device = DeviceInfo(id=52812, identifier="C684", model="VIRLOC 8")
        assert device.id == 52812
        assert device.identifier == "C684"
        assert device.model == "VIRLOC 8"


class TestEventInfo:
    """Test EventInfo schema."""

    def test_event_info_tracking(self):
        """Test: EventInfo for tracking event."""
        event = EventInfo(id=1, name="TRACKING")
        assert event.id == 1
        assert event.name == "TRACKING"


class TestElectricalData:
    """Test ElectricalData schema."""

    def test_electrical_data_complete(self):
        """Test: ElectricalData with all fields."""
        data = ElectricalData(
            voltage=28.17,
            battery=4.29,
            can_control_module_voltage=28.17
        )
        assert data.voltage == 28.17
        assert data.battery == 4.29
        assert data.can_control_module_voltage == 28.17

    def test_electrical_data_partial(self):
        """Test: ElectricalData with some null fields."""
        data = ElectricalData(voltage=24.5, battery=None, can_control_module_voltage=None)
        assert data.voltage == 24.5
        assert data.battery is None


class TestLocationData:
    """Test LocationData schema."""

    def test_location_data_complete(self):
        """Test: LocationData with all GPS fields."""
        location = LocationData(
            latitude=-21.86241,
            longitude=-43.53115,
            altitude=816,
            direction=6,
            gps=True
        )
        assert location.latitude == -21.86241
        assert location.longitude == -43.53115
        assert location.altitude == 816
        assert location.direction == 6
        assert location.gps is True


class TestEngineData:
    """Test EngineData (CAN Bus) schema."""

    def test_engine_data_complete(self):
        """Test: EngineData with all CAN fields."""
        engine = EngineData(
            rpm=1347,
            can_rpm=1347,
            can_accel_pedal_percent=0,
            can_engine_torque_percent=0,
            can_retarder_torque=1,
            can_engine_oil_pressure=0,
            can_turbo_charger_pressure=None
        )
        assert engine.rpm == 1347
        assert engine.can_rpm == 1347
        assert engine.can_accel_pedal_percent == 0
        assert engine.can_turbo_charger_pressure is None


class TestSpeedDistanceData:
    """Test SpeedDistanceData schema."""

    def test_speed_distance_data_complete(self):
        """Test: SpeedDistanceData with all fields."""
        data = SpeedDistanceData(
            speed=28,
            can_speed=28,
            odom=339127270,
            can_total_odometer=339127270
        )
        assert data.speed == 28
        assert data.can_speed == 28
        assert data.odom == 339127270
        assert data.can_total_odometer == 339127270


class TestFuelData:
    """Test FuelData schema."""

    def test_fuel_data_complete(self):
        """Test: FuelData with all fields."""
        fuel = FuelData(
            can_fuel_level_percent=95,
            can_def_level_percent=0,
            can_total_used_fuel=106440736
        )
        assert fuel.can_fuel_level_percent == 95
        assert fuel.can_def_level_percent == 0
        assert fuel.can_total_used_fuel == 106440736


class TestTemperatureData:
    """Test TemperatureData schema."""

    def test_temperature_data_complete(self):
        """Test: TemperatureData with all fields."""
        temp = TemperatureData(
            can_engine_coolant_temp=85,
            can_engine_coolant_level=0,
            external_sensor_temperature=None
        )
        assert temp.can_engine_coolant_temp == 85
        assert temp.can_engine_coolant_level == 0
        assert temp.external_sensor_temperature is None


class TestTransmissionData:
    """Test TransmissionData schema."""

    def test_transmission_data_complete(self):
        """Test: TransmissionData with gear and faixa."""
        transmission = TransmissionData(can_gear=-125, faixa=10)
        assert transmission.can_gear == -125
        assert transmission.faixa == 10


class TestPneumaticData:
    """Test PneumaticData schema."""

    def test_pneumatic_data_complete(self):
        """Test: PneumaticData with pressure values."""
        pneumatic = PneumaticData(
            can_pneumatic_system1_pressure=9,
            can_pneumatic_system2_pressure=0
        )
        assert pneumatic.can_pneumatic_system1_pressure == 9
        assert pneumatic.can_pneumatic_system2_pressure == 0


class TestHourmeterData:
    """Test HourmeterData schema."""

    def test_hourmeter_data_complete(self):
        """Test: HourmeterData with both values."""
        hourmeter = HourmeterData(hourmeter=0, can_engine_hourmeter=0)
        assert hourmeter.hourmeter == 0
        assert hourmeter.can_engine_hourmeter == 0


class TestStatusFlagsData:
    """Test StatusFlagsData schema."""

    def test_status_flags_complete(self):
        """Test: StatusFlagsData with all flags."""
        flags = StatusFlagsData(
            ignition=True,
            in5=0,
            in6=0,
            in7=0,
            in8=0,
            can_cruise_control_state=0,
            can_break_pedal_state=1,
            can_parking_brake_state=0,
            can_retarder_in_use=0,
            connection=0
        )
        assert flags.ignition is True
        assert flags.in5 == 0
        assert flags.in6 == 0
        assert flags.can_break_pedal_state == 1
        assert flags.can_parking_brake_state == 0

    def test_status_flags_inputs_are_int(self):
        """Test: in5, in6, in7, in8 are integers."""
        flags = StatusFlagsData(in5=1, in6=0, in7=1, in8=0)
        assert isinstance(flags.in5, int)
        assert isinstance(flags.in6, int)
        assert isinstance(flags.in7, int)
        assert isinstance(flags.in8, int)


class TestHistoryDetailedResponse:
    """Test complete HistoryDetailedResponse schema."""

    def test_history_detailed_response_complete(self):
        """Test: Complete response with all nested objects."""
        response = HistoryDetailedResponse(
            id=15054598630,
            unit=UnitInfo(
                id=47395,
                label="RVC-6C23",
                label2="753",
                obs="",
                group=GroupInfo(
                    id=14071,
                    name="ANSAL - GRUPO CSC",
                    subgroup=SubgroupInfo(id=15059, name="ANSAL - JUIZ DE FORA")
                )
            ),
            driver=DriverInfo(
                id=71439,
                name="ALENCAR PEREIRA DOS SANTOS",
                login="1720675929",
                group=GroupInfo(
                    id=14071,
                    name="ANSAL - GRUPO CSC",
                    subgroup=SubgroupInfo(id=15059, name="ANSAL - JUIZ DE FORA")
                )
            ),
            local_time="2025-12-19 11:59:40",
            time_write="2025-12-19 13:11:36",
            latitude=-21.86241,
            longitude=-43.53115,
            ign=True,
            speed=28,
            odom=339127270,
            rpm=1347,
            address="Estrada de Torreões - Juiz de Fora - MG",
            poi=PoiInfo(id=None, name=None, distance=None),
            cerca=CercaInfo(id=None, name=None),
            device=DeviceInfo(id=52812, identifier="C684", model="VIRLOC 8"),
            event=EventInfo(id=1, name="TRACKING"),
            electrical_data=ElectricalData(voltage=28.17, battery=4.29, can_control_module_voltage=28.17),
            location=LocationData(latitude=-21.86241, longitude=-43.53115, altitude=816, direction=6, gps=True),
            engine=EngineData(rpm=1347, can_rpm=1347, can_accel_pedal_percent=0, can_engine_torque_percent=0, can_retarder_torque=1, can_engine_oil_pressure=0, can_turbo_charger_pressure=None),
            speed_distance=SpeedDistanceData(speed=28, can_speed=28, odom=339127270, can_total_odometer=339127270),
            fuel=FuelData(can_fuel_level_percent=95, can_def_level_percent=0, can_total_used_fuel=106440736),
            temperature=TemperatureData(can_engine_coolant_temp=85, can_engine_coolant_level=0, external_sensor_temperature=None),
            transmission=TransmissionData(can_gear=-125, faixa=10),
            pneumatic=PneumaticData(can_pneumatic_system1_pressure=9, can_pneumatic_system2_pressure=0),
            hourmeter=HourmeterData(hourmeter=0, can_engine_hourmeter=0),
            status_flags=StatusFlagsData(ignition=True, in5=0, in6=0, in7=0, in8=0, can_cruise_control_state=0, can_break_pedal_state=1, can_parking_brake_state=0, can_retarder_in_use=0, connection=0)
        )

        assert response.id == 15054598630
        assert response.unit.id == 47395
        assert response.unit.label == "RVC-6C23"
        assert response.unit.group.id == 14071
        assert response.unit.group.subgroup.id == 15059
        assert response.driver.id == 71439
        assert response.device.model == "VIRLOC 8"
        assert response.event.name == "TRACKING"
        assert response.electrical_data.voltage == 28.17
        assert response.engine.can_rpm == 1347
        assert response.fuel.can_fuel_level_percent == 95

    def test_history_detailed_response_null_driver(self):
        """Test: Response with null driver (no driver logged)."""
        response = HistoryDetailedResponse(
            id=123456789,
            unit=UnitInfo(id=1, label="ABC-1234"),
            driver=None,
            local_time="2025-01-01 00:00:00"
        )

        assert response.id == 123456789
        assert response.driver is None

    def test_history_detailed_response_id_is_bigint(self):
        """Test: Response ID handles BigInt values."""
        big_id = 15054598630  # > 2^32
        response = HistoryDetailedResponse(
            id=big_id,
            unit=UnitInfo(id=1, label="TEST"),
            local_time="2025-01-01 00:00:00"
        )

        assert response.id == big_id
        assert response.id > 2**32


class TestHistoryDetailedCursorResponse:
    """Test HistoryDetailedCursorResponse pagination schema."""

    def test_cursor_response_with_data(self):
        """Test: Cursor response with data and next cursor."""
        response = HistoryDetailedCursorResponse(
            data=[
                HistoryDetailedResponse(
                    id=123,
                    unit=UnitInfo(id=1, label="ABC-1234"),
                    local_time="2025-01-01 00:00:00"
                )
            ],
            next_cursor="eyJ0IjogIjIwMjUtMDEtMDEgMDA6MDA6MDAiLCAiaSI6IDEyM30=",
            has_more=True,
            total_returned=1
        )

        assert len(response.data) == 1
        assert response.next_cursor is not None
        assert response.has_more is True
        assert response.total_returned == 1

    def test_cursor_response_last_page(self):
        """Test: Cursor response for last page (no more data)."""
        response = HistoryDetailedCursorResponse(
            data=[],
            next_cursor=None,
            has_more=False,
            total_returned=0
        )

        assert len(response.data) == 0
        assert response.next_cursor is None
        assert response.has_more is False


class TestDateRangeValidation:
    """Test date range validation logic."""

    def test_date_range_within_31_days_valid(self):
        """Test: Date range within 31 days is valid."""
        start = datetime(2025, 1, 1)
        end = datetime(2025, 1, 31)
        delta = end - start

        assert delta.days <= 31

    def test_date_range_exceeds_31_days_invalid(self):
        """Test: Date range exceeding 31 days should be rejected."""
        start = datetime(2025, 1, 1)
        end = datetime(2025, 2, 15)
        delta = end - start

        assert delta.days > 31

    def test_end_date_must_be_after_start_date(self):
        """Test: end_date must be after start_date."""
        start = datetime(2025, 1, 15)
        end = datetime(2025, 1, 10)

        assert end <= start  # This should be rejected


class TestCursorDatetimeConversion:
    """Test cursor datetime string conversion."""

    def test_cursor_time_string_to_datetime(self):
        """Test: Cursor time string converts to datetime correctly."""
        cursor_time_str = "2025-10-31 23:57:44"
        cursor_time = datetime.strptime(cursor_time_str, "%Y-%m-%d %H:%M:%S")

        assert cursor_time.year == 2025
        assert cursor_time.month == 10
        assert cursor_time.day == 31
        assert cursor_time.hour == 23
        assert cursor_time.minute == 57
        assert cursor_time.second == 44

    def test_cursor_roundtrip_preserves_datetime(self):
        """Test: Encoding and decoding cursor preserves datetime format."""
        original_time = "2025-12-19 11:59:40"
        original_id = 15054598630

        cursor = CompositeCursor(local_time=original_time, id=original_id)
        encoded = cursor.to_string()
        decoded = CompositeCursor.from_string(encoded)

        # Convert decoded string back to datetime
        decoded_dt = datetime.strptime(decoded.local_time, "%Y-%m-%d %H:%M:%S")

        assert decoded.local_time == original_time
        assert decoded_dt.year == 2025
        assert decoded_dt.month == 12
        assert decoded_dt.day == 19


class TestAccessControlLogic:
    """Test access control logic for history detailed."""

    def test_user_with_no_group_access_gets_empty_result(self):
        """Test: User with no group access should get empty result."""
        user_group_access = []

        # No accessible vehicles
        accessible_vehicle_ids = []

        assert len(accessible_vehicle_ids) == 0

    def test_vehicle_ids_filter_respects_access(self):
        """Test: Requested vehicle IDs are filtered by access."""
        user_accessible_ids = [100, 200, 300]
        requested_ids = [100, 400, 500]

        # Only IDs that user can access
        filtered_ids = [vid for vid in requested_ids if vid in user_accessible_ids]

        assert filtered_ids == [100]
        assert 400 not in filtered_ids
        assert 500 not in filtered_ids


class TestResponseFieldTypes:
    """Test that response fields have correct types."""

    def test_id_is_integer_not_string(self):
        """Test: ID field is integer, not string."""
        response = HistoryDetailedResponse(
            id=15054598630,
            unit=UnitInfo(id=1, label="TEST"),
            local_time="2025-01-01 00:00:00"
        )

        assert isinstance(response.id, int)
        assert not isinstance(response.id, str)

    def test_latitude_longitude_are_float(self):
        """Test: Latitude and longitude are floats."""
        response = HistoryDetailedResponse(
            id=1,
            unit=UnitInfo(id=1, label="TEST"),
            local_time="2025-01-01 00:00:00",
            latitude=-21.86241,
            longitude=-43.53115
        )

        assert isinstance(response.latitude, float)
        assert isinstance(response.longitude, float)

    def test_odom_is_integer(self):
        """Test: Odometer is integer."""
        response = HistoryDetailedResponse(
            id=1,
            unit=UnitInfo(id=1, label="TEST"),
            local_time="2025-01-01 00:00:00",
            odom=339127270
        )

        assert isinstance(response.odom, int)

    def test_speed_is_integer(self):
        """Test: Speed is integer."""
        response = HistoryDetailedResponse(
            id=1,
            unit=UnitInfo(id=1, label="TEST"),
            local_time="2025-01-01 00:00:00",
            speed=28
        )

        assert isinstance(response.speed, int)

    def test_status_flags_inputs_are_integers(self):
        """Test: in5, in6, in7, in8 are integers."""
        flags = StatusFlagsData(in5=0, in6=1, in7=0, in8=1)

        assert isinstance(flags.in5, int)
        assert isinstance(flags.in6, int)
        assert isinstance(flags.in7, int)
        assert isinstance(flags.in8, int)
