"""Focused tests for the ISYGLT IPMS integration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.isyglt_ipms.api import IsygltApiClient
from custom_components.isyglt_ipms.binary_sensor import (
    IsygltBinarySensor,
    IsygltMasterConnectivitySensor,
)
from custom_components.isyglt_ipms.const import DEFAULT_PULSE_MS
from custom_components.isyglt_ipms.config_flow import _validate_input
from custom_components.isyglt_ipms.cover import IsygltCover
from custom_components.isyglt_ipms.button import IsygltButton
from custom_components.isyglt_ipms.climate import IsygltClimate
from custom_components.isyglt_ipms.light import IsygltLight
from custom_components.isyglt_ipms.manifest_loader import (
    _load_manifest,
    _validate_manifest,
)
from custom_components.isyglt_ipms.numeric import decode_numeric, encode_numeric
from custom_components.isyglt_ipms.number import IsygltNumber
from custom_components.isyglt_ipms.sensor import IsygltSensor
from custom_components.isyglt_ipms.switch import IsygltSwitch


ROOT = Path(__file__).parents[1]


class FakeResponse:
    """Minimal async response fake for aiohttp calls."""

    headers = {"$PROXY_RESPONSE$": "OK"}
    payload: dict[str, Any] = {
        "poolStatus": "OK",
        "dataItems": [{"m": "IPM1:NA64.1", "v": "1"}],
        "masterCycleSet": {"IPM001": 123},
    }
    json_content_type: str | None = "not-called"

    def raise_for_status(self) -> None:
        """Match aiohttp response interface."""

    async def json(self, *, content_type: str | None = "application/json") -> dict[str, Any]:
        self.json_content_type = content_type
        return self.payload

    async def text(self) -> str:
        return ""

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


class FakeSession:
    """Capture outgoing API calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.response = FakeResponse()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


class FakeCoordinator:
    """Small coordinator substitute for entity-level tests."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data = data or {}
        self.master = {
            "alias": "IPM1",
            "ipms_url": "http://localhost:8080/ipms",
            "pulse_ms": DEFAULT_PULSE_MS,
        }
        self.manifest = {"locations": []}
        self.last_update_success = True
        self.master_online = True
        self.commands: list[tuple[Any, ...]] = []

    def async_add_listener(self, _callback: Any, _context: Any = None) -> Any:
        return lambda: None

    def get_bit(self, marker: str) -> bool | None:
        raw = self.data.get(marker)
        return int(raw) != 0 if raw is not None else None

    def get_byte(self, marker: str) -> int | None:
        raw = self.data.get(marker)
        return int(raw) if raw is not None else None

    def get_int(self, marker: str) -> int | None:
        return self.get_byte(marker)

    async def async_write_bit(self, marker: str, value: bool) -> None:
        self.commands.append(("bit", marker, value))

    async def async_write_byte(self, marker: str, value: int) -> None:
        self.commands.append(("byte", marker, value))

    async def async_write_word(self, marker: str, value: int) -> None:
        self.commands.append(("word", marker, value))

    async def async_pulse(self, marker: str, pulse_ms: int | None = None) -> None:
        self.commands.append(("pulse", marker, pulse_ms))


def _entity(
    entity_class: type[Any],
    coordinator: FakeCoordinator,
    config: dict[str, Any],
) -> Any:
    return entity_class(coordinator, config, {})


def test_ajax_command_uses_required_query_and_form_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        session = FakeSession()
        monkeypatch.setattr(
            "custom_components.isyglt_ipms.api.async_get_clientsession",
            lambda hass: session,
        )
        client = IsygltApiClient(
            object.__new__(HomeAssistant), "http://host/ipms"
        )
        await client.async_read_pool([("IPM1:NA64.1", "BIT")])
        await client.async_send_command("WRITE_BYTE", "IPM1:M56", 180)
        await client.async_pulse_bit("IPM1:NE1.1", 0)

        read_url, read_request = session.calls[0]
        assert read_url == "http://host/ipms/jsonpooldata"
        assert read_request["json"]["reqDataSet"] == [
            {"masterMarker": "IPM1:NA64.1", "valueType": "BIT", "length": 1}
        ]
        url, request = session.calls[1]
        assert url == "http://host/ipms/ajaxcmdproxy"
        assert request["params"] == {"$MCRQC$": "1"}
        assert request["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert request["data"]["CMD"] == "WRITE_BYTE"
        assert request["data"]["MARKER"] == "IPM1:M56"
        assert request["data"]["VALUE"] == "180"
        assert session.calls[2][1]["data"]["CMD"] == "SET_BIT_SWITCH"
        assert session.calls[3][1]["data"]["CMD"] == "CLR_BIT_SWITCH"

    asyncio.run(run())


def test_config_flow_connection_check_reads_dummy_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_markers: list[tuple[str, str]] = []

    class FakeApi:
        def __init__(self, hass: Any, base_url: str) -> None:
            pass

        async def async_read_pool(
            self, markers: list[tuple[str, str]]
        ) -> dict[str, Any]:
            received_markers.extend(markers)
            return {}

    async def load_manifest(hass: Any, path: Path) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(
        "custom_components.isyglt_ipms.config_flow.async_load_manifest",
        load_manifest,
    )
    monkeypatch.setattr(
        "custom_components.isyglt_ipms.config_flow.IsygltApiClient", FakeApi
    )

    async def run() -> None:
        hass = type("Hass", (), {"config": type("Config", (), {"config_dir": "/config"})()})()
        await _validate_input(
            hass,
            {
                "manifest_path": "anlage.yaml",
                "ipms_url": "http://host/ipms",
                "master_alias": "IPM2",
            },
        )

    asyncio.run(run())
    assert received_markers == [("IPM2:M0", "BYTE")]


@pytest.mark.parametrize(
    ("field", "item"),
    [
        ("responseDataSet", {"masterMarker": "IPM1:M56", "value": "180"}),
        ("dataItems", {"m": "IPM1:M56", "v": "180"}),
        ("responseDataSet", {"m": "IPM1:M56", "v": "180"}),
        ("dataItems", {"masterMarker": "IPM1:M56", "value": "180"}),
    ],
)
def test_jsonpooldata_supports_current_and_legacy_response_shapes(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    item: dict[str, str],
) -> None:
    async def run() -> None:
        session = FakeSession()
        session.response.payload = {
            "poolStatus": "OK",
            field: [item],
            "masterCycleSet": {"IPM001": 123},
        }
        monkeypatch.setattr(
            "custom_components.isyglt_ipms.api.async_get_clientsession",
            lambda hass: session,
        )
        client = IsygltApiClient(
            object.__new__(HomeAssistant), "http://host/ipms"
        )

        result = await client.async_read_pool([("IPM1:M56", "BYTE")])

        assert result["data"] == {"IPM1:M56": "180"}
        assert result["poolStatus"] == "OK"
        assert result["masterCycleSet"] == {"IPM001": 123}
        assert session.response.json_content_type is None

    asyncio.run(run())


def test_example_manifest_is_valid_and_uses_manifest_field_names() -> None:
    manifest = _load_manifest(ROOT / "examples/anlage.example.yaml")
    _validate_manifest(manifest)
    assert manifest["entities"][0]["state_marker"] == "IPM1:NA64.1"
    assert manifest["entities"][1]["enable_marker"] == "IPM1:NE64.1"
    assert "location_id" in manifest["entities"][1]


def test_entities_from_example_instantiate_with_home_assistant_platforms() -> None:
    manifest = _load_manifest(ROOT / "examples/anlage.example.yaml")
    classes = {
        "light": IsygltLight,
        "switch": IsygltSwitch,
        "cover": IsygltCover,
        "button": IsygltButton,
        "number": IsygltNumber,
        "climate": IsygltClimate,
        "sensor": IsygltSensor,
        "binary_sensor": IsygltBinarySensor,
    }
    coordinator = FakeCoordinator()
    entities = [
        _entity(classes[config["platform"]], coordinator, config)
        for config in manifest["entities"]
    ]
    assert len(entities) == len(manifest["entities"])


def test_coordinator_collects_markers_deduplicated_in_one_batch() -> None:
    from custom_components.isyglt_ipms.coordinator import IsygltDataUpdateCoordinator

    entities = [
        {
            "platform": "light",
            "mode": "dimmer",
            "brightness_marker": "IPM1:M56",
            "enable_marker": "IPM1:NE64.1",
            "fault_marker": "IPM1:NE64.2",
        },
        {
            "platform": "switch",
            "state_marker": "IPM1:NE64.1",
        },
    ]
    assert dict(IsygltDataUpdateCoordinator._collect_markers(entities)) == {
        "IPM1:M56": "BYTE",
        "IPM1:NE64.1": "BIT",
        "IPM1:NE64.2": "BIT",
    }


@pytest.mark.parametrize(
    ("raw", "mode", "expected"),
    [
        (255, "signed_byte", -1),
        (128, "signed_byte", -128),
        (65535, "signed_word", -1),
        (32768, "signed_word", -32768),
        (65535, "word", 65535),
    ],
)
def test_numeric_decode_signed_and_word_values(
    raw: int, mode: str, expected: int
) -> None:
    assert decode_numeric(raw, mode) == expected


def test_signed_numeric_write_uses_twos_complement() -> None:
    assert encode_numeric(-1, "signed_byte", 1, 0) == 255
    assert encode_numeric(-1, "signed_word", 1, 0) == 65535


def test_dimmer_remembers_brightness_when_turned_off() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator({"IPM1:M56": "112"})
        light = _entity(
            IsygltLight,
            coordinator,
            {
                "id": "dimmer",
                "name": "Dimmer",
                "mode": "dimmer",
                "brightness_marker": "IPM1:M56",
            },
        )
        assert light.brightness == 112
        await light.async_turn_off()
        assert coordinator.commands[-1] == ("byte", "IPM1:M56", 0)
        await light.async_turn_on()
        assert coordinator.commands[-1] == ("byte", "IPM1:M56", 112)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("config", "marker_values", "reason"),
    [
        ({"enable_marker": "IPM1:NE1.1"}, {"IPM1:NE1.1": "0"}, "disabled"),
        ({"fault_marker": "IPM1:NE1.2"}, {"IPM1:NE1.2": "1"}, "fault"),
    ],
)
def test_enable_and_fault_markers_make_entity_unavailable(
    config: dict[str, str], marker_values: dict[str, str], reason: str
) -> None:
    coordinator = FakeCoordinator(marker_values)
    entity = _entity(
        IsygltBinarySensor,
        coordinator,
        {
            "id": "binary",
            "name": "Binary",
            "state_marker": "IPM1:M1.1",
            **config,
        },
    )
    assert entity.available is False
    assert entity.extra_state_attributes == {"lock_reason": reason}


def test_master_connectivity_sensor_reports_cycle_watchdog() -> None:
    coordinator = FakeCoordinator()
    entity = IsygltMasterConnectivitySensor(coordinator)
    assert entity.is_on is True
    coordinator.master_online = False
    assert entity.is_on is False
    assert entity.available is True


def test_number_reads_signed_word_and_writes_word() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator({"IPM1:M101": "65535"})
        number = _entity(
            IsygltNumber,
            coordinator,
            {
                "id": "setting",
                "name": "Signed setting",
                "mode": "signed_word",
                "marker": "IPM1:M101",
                "min_value": -100,
                "max_value": 100,
            },
        )
        assert number.native_value == -1
        await number.async_set_native_value(-10)
        assert coordinator.commands[-1] == ("word", "IPM1:M101", 65526)

    asyncio.run(run())


def test_cover_stop_repeats_last_direction_impulse() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator()
        cover = _entity(
            IsygltCover,
            coordinator,
            {
                "id": "cover",
                "name": "Shade",
                "up_marker": "IPM1:NE1.1",
                "down_marker": "IPM1:NE1.2",
            },
        )
        await cover.async_close_cover()
        await cover.async_stop_cover()
        assert coordinator.commands[-1] == (
            "pulse",
            "IPM1:NE1.2",
            DEFAULT_PULSE_MS,
        )

    asyncio.run(run())


def test_cover_without_position_reports_unknown_closed_state() -> None:
    cover = _entity(
        IsygltCover,
        FakeCoordinator(),
        {
            "id": "cover",
            "name": "Shade",
            "up_marker": "IPM1:NE1.1",
            "down_marker": "IPM1:NE1.2",
        },
    )
    assert cover.is_closed is None
    assert cover.state is None


def test_cover_tilt_sends_two_impulses() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator()
        cover = _entity(
            IsygltCover,
            coordinator,
            {
                "id": "blind",
                "name": "Blind",
                "up_marker": "IPM1:NE1.1",
                "down_marker": "IPM1:NE1.2",
                "tilt_step_ms": 1,
            },
        )
        await cover._async_tilt("up")
        assert coordinator.commands == [
            ("pulse", "IPM1:NE1.1", DEFAULT_PULSE_MS),
            ("pulse", "IPM1:NE1.1", DEFAULT_PULSE_MS),
        ]

    asyncio.run(run())


def test_sensor_combines_high_and_low_byte_with_scale() -> None:
    coordinator = FakeCoordinator({"IPM1:NE50": "1", "IPM1:NE51": "44"})
    sensor = _entity(
        IsygltSensor,
        coordinator,
        {
            "id": "lux",
            "name": "Lux",
            "mode": "word",
            "high_marker": "IPM1:NE50",
            "low_marker": "IPM1:NE51",
            "scale": 0.5,
            "offset": 1,
        },
    )
    assert sensor.native_value == 151.0


def test_switch_reads_state_and_writes_command_marker() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator({"IPM1:M1.1": "1"})
        switch = _entity(
            IsygltSwitch,
            coordinator,
            {
                "id": "switch",
                "name": "Switch",
                "state_marker": "IPM1:M1.1",
                "command_marker": "IPM1:NA1.1",
            },
        )
        assert switch.is_on is True
        await switch.async_turn_off()
        assert coordinator.commands[-1] == ("bit", "IPM1:NA1.1", False)

    asyncio.run(run())


def test_button_uses_entity_pulse_override() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator()
        button = _entity(
            IsygltButton,
            coordinator,
            {
                "id": "button",
                "name": "Button",
                "command_marker": "IPM1:NE1.1",
                "pulse_ms": 75,
            },
        )
        await button.async_press()
        assert coordinator.commands[-1] == ("pulse", "IPM1:NE1.1", 75)

    asyncio.run(run())


def test_climate_scales_temperature_and_writes_setpoint() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator(
            {
                "IPM1:M96": "40",
                "IPM1:M92": "42",
                "IPM1:NE1.1": "0",
            }
        )
        climate = _entity(
            IsygltClimate,
            coordinator,
            {
                "id": "thermostat",
                "name": "Thermostat",
                "current_temp_marker": "IPM1:M96",
                "target_temp_marker": "IPM1:M92",
                "comfort_marker": "IPM1:NE1.1",
                "temperature_scale": 0.5,
            },
        )
        assert climate.current_temperature == 20
        assert climate.target_temperature == 21
        assert climate.hvac_mode.value == "auto"
        await climate.async_set_temperature(temperature=22.5)
        assert coordinator.commands[-1] == ("byte", "IPM1:M92", 45)

    asyncio.run(run())
