"""Focused tests for the ISYGLT IPMS integration."""

from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlsplit

import aiohttp
import pytest
import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError

import custom_components.isyglt_ipms as integration
from custom_components.isyglt_ipms.api import (
    IsygltApiClient,
    IsygltCommandError,
    IsygltConnectionError,
    normalize_ipms_url,
)
from custom_components.isyglt_ipms.binary_sensor import (
    IsygltBinarySensor,
    IsygltMasterConnectivitySensor,
)
from custom_components.isyglt_ipms.const import DEFAULT_PULSE_MS, DOMAIN
from custom_components.isyglt_ipms.const import (
    CONF_IPMS_API_PATH,
    CONF_IPMS_HOST,
    CONF_IPMS_PORT,
    CONF_IPMS_SCHEME,
    CONF_MANIFEST_PATH,
    CONF_MASTER_ALIAS,
    DEFAULT_IPMS_API_PATH,
    DEFAULT_IPMS_PORT,
    DEFAULT_IPMS_SCHEME,
    DEFAULT_MANIFEST_PATH,
)
from custom_components.isyglt_ipms.config_flow import (
    IsygltConfigFlow,
    IsygltOptionsFlow,
    build_ipms_url,
    _connection_error_key,
    _config_entry_unique_id,
    _form_schema,
    get_config_entry_settings,
    _manifest_error_key,
    _validate_input,
)
from custom_components.isyglt_ipms.cover import IsygltCover
from custom_components.isyglt_ipms.button import IsygltButton
from custom_components.isyglt_ipms.climate import IsygltClimate
from custom_components.isyglt_ipms.coordinator import IsygltDataUpdateCoordinator
from custom_components.isyglt_ipms.light import IsygltLight
from custom_components.isyglt_ipms.manifest_loader import (
    _load_manifest,
    _validate_manifest,
    resolve_manifest_path,
    validate_manifest_master,
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
        self.optimistic_writes: list[tuple[str, int]] = []

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

    async def async_write_bit(
        self,
        marker: str,
        value: bool,
        *,
        optimistic_marker: str | None = None,
    ) -> None:
        self.commands.append(("bit", marker, value))
        state_marker = optimistic_marker or marker
        self.data[state_marker] = str(int(value))
        self.optimistic_writes.append((state_marker, int(value)))

    async def async_write_byte(
        self,
        marker: str,
        value: int,
        *,
        optimistic_marker: str | None = None,
    ) -> None:
        self.commands.append(("byte", marker, value))
        state_marker = optimistic_marker or marker
        self.data[state_marker] = str(value)
        self.optimistic_writes.append((state_marker, value))

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


def _optimistic_coordinator(api: Any, *, hold_seconds: float = 1.0) -> Any:
    """Create a coordinator instance with the state needed for write-path tests."""
    coordinator = object.__new__(IsygltDataUpdateCoordinator)
    coordinator.api = api
    coordinator.data = {"IPM1:M1": "0", "IPM1:M2": "0"}
    coordinator._marker_command_locks = {}
    coordinator._optimistic_states = {}
    coordinator._optimistic_generations = {}
    coordinator._optimistic_expiry_handles = {}
    coordinator._optimistic_hold_seconds = hold_seconds
    coordinator.async_update_listeners = lambda: None

    async def request_refresh() -> None:
        return None

    coordinator.async_request_refresh = request_refresh
    return coordinator


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
            object.__new__(HomeAssistant), "http://host"
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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://192.168.178.71:8080", "http://192.168.178.71:8080/ipms"),
        ("http://192.168.178.71:8080/", "http://192.168.178.71:8080/ipms"),
        ("http://192.168.178.71:8080/ipms/", "http://192.168.178.71:8080/ipms"),
        ("https://ipms.example/proxy/ipms", "https://ipms.example/proxy/ipms"),
    ],
)
def test_ipms_url_normalization(url: str, expected: str) -> None:
    assert normalize_ipms_url(url) == expected


def test_manifest_paths_support_absolute_config_paths_and_reject_escape() -> None:
    assert resolve_manifest_path(
        "/config", "/config/isyglt/ipm1_eg.yaml"
    ) == Path("/config/isyglt/ipm1_eg.yaml")
    assert resolve_manifest_path(
        "/config", "isyglt/ipm2_og.yaml"
    ) == Path("/config/isyglt/ipm2_og.yaml")
    with pytest.raises(
        ConfigEntryError, match="inside the Home Assistant config directory"
    ):
        resolve_manifest_path("/config", "/tmp/ipm.yaml")


def test_manifest_master_validation_prevents_cross_master_markers() -> None:
    manifest = {
        "master": {"alias": "IPM1"},
        "entities": [
            {"id": "light", "state_marker": "IPM1:NA1.1"},
        ],
    }
    validate_manifest_master(manifest, "IPM1")
    with pytest.raises(ConfigEntryError, match="must belong to master"):
        validate_manifest_master(
            {
                "master": {"alias": "IPM1"},
                "entities": [
                    {"id": "light", "state_marker": "IPM2:NA1.1"},
                ],
            },
            "IPM1",
        )


def test_config_entry_unique_ids_allow_multiple_masters_on_one_server() -> None:
    first = _config_entry_unique_id("http://192.168.178.71:8080", "IPM1")
    assert first == "http://192.168.178.71:8080/ipms#IPM1"
    assert _config_entry_unique_id(
        "http://192.168.178.71:8080", "IPM2"
    ) != first
    assert _config_entry_unique_id(
        "http://192.168.178.71:8080", "IPM2"
    ) != first
    assert _config_entry_unique_id(
        "http://192.168.178.71:8080/", "IPM1"
    ) == first


def test_options_override_connection_fields_and_legacy_entries_are_supported() -> None:
    legacy_entry = SimpleNamespace(
        data={
            "ipms_url": "https://192.168.178.71:8443/custom/ipms",
            CONF_MASTER_ALIAS: "IPM1",
            CONF_MANIFEST_PATH: "isyglt/ipm1.yaml",
        },
        options={},
    )
    legacy_settings = get_config_entry_settings(legacy_entry)
    assert build_ipms_url(legacy_settings) == (
        "https://192.168.178.71:8443/custom/ipms"
    )

    updated_entry = SimpleNamespace(
        data=legacy_entry.data,
        options={
            CONF_IPMS_SCHEME: "http://",
            CONF_IPMS_HOST: "192.168.178.72",
            CONF_IPMS_PORT: 8080,
            CONF_IPMS_API_PATH: "/ipms",
        },
    )
    assert build_ipms_url(get_config_entry_settings(updated_entry)) == (
        "http://192.168.178.72:8080/ipms"
    )
    assert callable(
        IsygltConfigFlow.async_get_options_flow(
            cast(Any, SimpleNamespace())
        ).async_step_init
    )


@pytest.mark.parametrize(
    ("scheme", "host", "port", "api_path", "expected"),
    [
        ("http://", "192.168.178.71", 8080, "/ipms", "http://192.168.178.71:8080/ipms"),
        ("https://", "ipms.example", 8443, "/custom/api", "https://ipms.example:8443/custom/api"),
        ("http://", "2001:db8::1", 8080, "/ipms", "http://[2001:db8::1]:8080/ipms"),
    ],
)
def test_build_ipms_url_from_separate_fields(
    scheme: str, host: str, port: int, api_path: str, expected: str
) -> None:
    assert build_ipms_url(
        {
            CONF_IPMS_SCHEME: scheme,
            CONF_IPMS_HOST: host,
            CONF_IPMS_PORT: port,
            CONF_IPMS_API_PATH: api_path,
        }
    ) == expected


def test_config_form_prefills_protocol_port_api_path_and_manifest_directory() -> None:
    schema = _form_schema({})
    defaults = {
        marker.schema: marker.default()
        for marker in schema.schema
        if isinstance(marker, vol.Required)
    }
    assert defaults[CONF_IPMS_SCHEME] == DEFAULT_IPMS_SCHEME
    assert defaults[CONF_IPMS_PORT] == DEFAULT_IPMS_PORT
    assert defaults[CONF_IPMS_API_PATH] == DEFAULT_IPMS_API_PATH
    assert defaults[CONF_MANIFEST_PATH] == DEFAULT_MANIFEST_PATH


def test_connection_and_manifest_errors_are_specific() -> None:
    refused = ConnectionRefusedError(errno.ECONNREFUSED, "connection refused")
    connection_key = aiohttp.client_reqrep.ConnectionKey(
        "host", 8080, False, None, None, None, None
    )
    connector_error = aiohttp.ClientConnectorError(connection_key, refused)
    api_error = IsygltConnectionError("failed")
    api_error.__cause__ = connector_error
    assert _connection_error_key(api_error) == "connection_refused"

    timeout_error = IsygltConnectionError("failed")
    timeout_error.__cause__ = TimeoutError()
    assert _connection_error_key(timeout_error) == "connection_timeout"

    missing_manifest = ConfigEntryError("missing")
    missing_manifest.__cause__ = FileNotFoundError("missing")
    assert _manifest_error_key(missing_manifest) == "manifest_not_found"
    assert _manifest_error_key(ConfigEntryError("invalid yaml")) == "invalid_manifest"
    assert issubclass(IsygltOptionsFlow, config_entries.OptionsFlowWithReload)


def test_two_config_entries_keep_coordinators_independent_and_share_areas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifests = {
        "ipm1.yaml": {
            "master": {"alias": "IPM1"},
            "locations": [{"id": "eg_wz", "name": "Wohnzimmer"}],
            "entities": [],
        },
        "ipm2.yaml": {
            "master": {"alias": "IPM2"},
            "locations": [{"id": "og_wz", "name": "Wohnzimmer"}],
            "entities": [],
        },
    }

    async def load_manifest(hass: Any, path: Path) -> dict[str, Any]:
        return manifests[path.name]

    class FakeApi:
        def __init__(self, hass: Any, base_url: str) -> None:
            self.base_url = base_url

    coordinators: list[Any] = []

    class FakeCoordinator:
        def __init__(
            self,
            hass: Any,
            api: FakeApi,
            manifest: dict[str, Any],
            master: dict[str, Any],
            *,
            config_entry: Any,
        ) -> None:
            self.api = api
            self.manifest = manifest
            self.master = master
            self.config_entry = config_entry
            coordinators.append(self)

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeAreaRegistry:
        def __init__(self) -> None:
            self.areas: dict[str, Any] = {}

        def async_get_area_by_name(self, name: str) -> Any:
            return self.areas.get(name)

        def async_create(self, name: str) -> Any:
            area = SimpleNamespace(id=f"area_{len(self.areas) + 1}")
            self.areas[name] = area
            return area

    area_registry = FakeAreaRegistry()

    class FakeConfigEntries:
        async def async_forward_entry_setups(
            self, entry: Any, platforms: Any
        ) -> None:
            return None

        async def async_unload_platforms(self, entry: Any, platforms: Any) -> bool:
            return True

    monkeypatch.setattr(integration, "async_load_manifest", load_manifest)
    monkeypatch.setattr(integration, "IsygltApiClient", FakeApi)
    monkeypatch.setattr(integration, "IsygltDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(integration.area_registry, "async_get", lambda hass: area_registry)

    async def run() -> None:
        hass = SimpleNamespace(
            config=SimpleNamespace(config_dir="/config"),
            config_entries=FakeConfigEntries(),
            data={},
        )
        entries = [
            SimpleNamespace(
                entry_id=master_alias,
                data={
                    CONF_MANIFEST_PATH: f"/config/{manifest_name}",
                    CONF_IPMS_SCHEME: "http://",
                    CONF_IPMS_HOST: urlsplit(server_url).hostname,
                    CONF_IPMS_PORT: urlsplit(server_url).port,
                    CONF_IPMS_API_PATH: "/ipms",
                    CONF_MASTER_ALIAS: master_alias,
                },
                options={},
            )
            for master_alias, manifest_name, server_url in (
                ("IPM1", "ipm1.yaml", "http://192.168.178.71:8080"),
                ("IPM2", "ipm2.yaml", "http://192.168.178.71:8080"),
            )
        ]
        await integration.async_setup_entry(hass, entries[0])
        await integration.async_setup_entry(hass, entries[1])

        first = hass.data[DOMAIN][entries[0].entry_id]
        second = hass.data[DOMAIN][entries[1].entry_id]
        assert first["coordinator"] is not second["coordinator"]
        assert first["api"] is not second["api"]
        assert first["api"].base_url == "http://192.168.178.71:8080/ipms"
        assert second["api"].base_url == "http://192.168.178.71:8080/ipms"
        assert first["areas"]["eg_wz"] == second["areas"]["og_wz"]

        await integration.async_unload_entry(hass, entries[0])
        assert entries[0].entry_id not in hass.data[DOMAIN]
        assert entries[1].entry_id in hass.data[DOMAIN]

    asyncio.run(run())
    assert len(coordinators) == 2


def test_config_flow_connection_check_reads_dummy_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_markers: list[tuple[str, str]] = []

    api_urls: list[str] = []

    class FakeApi:
        def __init__(self, hass: Any, base_url: str) -> None:
            api_urls.append(base_url)

        async def async_read_pool(
            self, markers: list[tuple[str, str]]
        ) -> dict[str, Any]:
            received_markers.extend(markers)
            return {}

    loaded_paths: list[Path] = []

    async def load_manifest(hass: Any, path: Path) -> dict[str, Any]:
        loaded_paths.append(path)
        return {"master": {"alias": "IPM2"}, "entities": []}

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
            cast(HomeAssistant, hass),
            {
                CONF_MANIFEST_PATH: "/config/isyglt/ipm2_og.yaml",
                CONF_IPMS_SCHEME: "http://",
                CONF_IPMS_HOST: "host",
                CONF_IPMS_PORT: 8080,
                CONF_IPMS_API_PATH: "/ipms",
                CONF_MASTER_ALIAS: "IPM2",
            },
        )

    asyncio.run(run())
    assert received_markers == [("IPM2:M0", "BYTE")]
    assert loaded_paths == [Path("/config/isyglt/ipm2_og.yaml")]
    assert api_urls == ["http://host:8080/ipms"]


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
        assert light.is_on is False
        await light.async_turn_on()
        assert coordinator.commands[-1] == ("byte", "IPM1:M56", 112)
        assert light.is_on is True
        assert coordinator.optimistic_writes[-1] == ("IPM1:M56", 112)

    asyncio.run(run())


def test_tunable_white_uses_optimistic_brightness_and_color_temperature() -> None:
    async def run() -> None:
        coordinator = FakeCoordinator(
            {"IPM1:M32": "0", "IPM1:M33": "0"}
        )
        light = _entity(
            IsygltLight,
            coordinator,
            {
                "id": "tw",
                "name": "Tunable white",
                "mode": "dimmer_tw",
                "brightness_marker": "IPM1:M32",
                "color_temp_marker": "IPM1:M33",
                "min_kelvin": 2700,
                "max_kelvin": 6500,
            },
        )

        await light.async_turn_on(
            **{ATTR_BRIGHTNESS: 80, ATTR_COLOR_TEMP_KELVIN: 4600}
        )

        assert light.brightness == 80
        assert light.color_temp_kelvin == 4607
        assert coordinator.optimistic_writes == [
            ("IPM1:M32", 80),
            ("IPM1:M33", 128),
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("mode", "color_key", "color", "fields"),
    [
        (
            "rgb",
            ATTR_RGB_COLOR,
            (12, 34, 56),
            ("red_marker", "green_marker", "blue_marker"),
        ),
        (
            "rgbw",
            ATTR_RGBW_COLOR,
            (12, 34, 56, 78),
            ("red_marker", "green_marker", "blue_marker", "white_marker"),
        ),
    ],
)
def test_rgb_and_rgbw_use_optimistic_color_marker_values(
    mode: str,
    color_key: str,
    color: tuple[int, ...],
    fields: tuple[str, ...],
) -> None:
    async def run() -> None:
        config: dict[str, Any] = {
            "id": mode,
            "name": mode.upper(),
            "mode": mode,
        }
        config.update(
            {field: f"IPM1:M{index}" for index, field in enumerate(fields, 40)}
        )
        data = {config[field]: "0" for field in fields}
        coordinator = FakeCoordinator(data)
        light = _entity(IsygltLight, coordinator, config)

        await light.async_turn_on(**{color_key: color})

        expected = list(zip((config[field] for field in fields), color, strict=True))
        assert coordinator.optimistic_writes == expected
        assert light.rgb_color == color if mode == "rgb" else light.rgbw_color == color

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


def test_optimistic_state_is_visible_before_refresh_completes() -> None:
    async def run() -> None:
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()

        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                return None

        coordinator = _optimistic_coordinator(Api())

        async def request_refresh() -> None:
            refresh_started.set()
            await release_refresh.wait()

        coordinator.async_request_refresh = request_refresh
        write = asyncio.create_task(
            coordinator.async_write_bit("IPM1:M1", True)
        )
        await refresh_started.wait()
        assert coordinator.get_bit("IPM1:M1") is True
        release_refresh.set()
        await write

    asyncio.run(run())


def test_optimistic_state_is_visible_while_http_command_is_pending() -> None:
    async def run() -> None:
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                send_started.set()
                await release_send.wait()

        coordinator = _optimistic_coordinator(Api())
        write = asyncio.create_task(
            coordinator.async_write_bit("IPM1:M1", True)
        )
        await send_started.wait()

        assert coordinator.get_bit("IPM1:M1") is True
        assert coordinator._optimistic_states["IPM1:M1"][1] is None

        release_send.set()
        await write
        assert coordinator._optimistic_states["IPM1:M1"][1] is not None

    asyncio.run(run())


def test_failed_pending_send_clears_immediate_optimistic_state() -> None:
    async def run() -> None:
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                send_started.set()
                await release_send.wait()
                raise IsygltCommandError("connection refused")

        coordinator = _optimistic_coordinator(Api())
        write = asyncio.create_task(
            coordinator.async_write_bit("IPM1:M1", True)
        )
        await send_started.wait()
        assert coordinator.get_bit("IPM1:M1") is True

        release_send.set()
        with pytest.raises(HomeAssistantError):
            await write

        assert coordinator.get_bit("IPM1:M1") is False
        assert "IPM1:M1" not in coordinator._optimistic_states

    asyncio.run(run())


def test_byte_optimistic_state_overrides_stale_pool_during_pending_send() -> None:
    async def run() -> None:
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                send_started.set()
                await release_send.wait()

        coordinator = _optimistic_coordinator(Api())
        coordinator.data["IPM1:M1"] = "25"
        write = asyncio.create_task(
            coordinator.async_write_byte("IPM1:M1", 220)
        )
        await send_started.wait()

        assert coordinator.data["IPM1:M1"] == "25"
        assert coordinator.get_byte("IPM1:M1") == 220

        release_send.set()
        await write

    asyncio.run(run())


def test_failed_send_does_not_set_optimistic_state() -> None:
    async def run() -> None:
        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                raise IsygltCommandError("connection refused")

        coordinator = _optimistic_coordinator(Api())
        with pytest.raises(HomeAssistantError):
            await coordinator.async_write_bit("IPM1:M1", True)
        assert coordinator.get_bit("IPM1:M1") is False
        assert coordinator._optimistic_states == {}

    asyncio.run(run())


def test_concurrent_writes_to_same_marker_are_serialized_and_latest_wins() -> None:
    async def run() -> None:
        commands: list[bool] = []
        in_flight = 0
        max_in_flight = 0

        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                nonlocal in_flight, max_in_flight
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                commands.append(cmd == "SET_BIT_SWITCH")
                await asyncio.sleep(0.01)
                in_flight -= 1

        coordinator = _optimistic_coordinator(Api())
        await asyncio.gather(
            coordinator.async_write_bit("IPM1:M1", True),
            coordinator.async_write_bit("IPM1:M1", False),
        )

        assert commands == [True, False]
        assert max_in_flight == 1
        assert coordinator.get_bit("IPM1:M1") is False

    asyncio.run(run())


def test_optimistic_state_expires_after_hold_timer() -> None:
    async def run() -> None:
        class Api:
            async def async_send_command(
                self, cmd: str, marker: str, value: int
            ) -> None:
                return None

        coordinator = _optimistic_coordinator(Api(), hold_seconds=0.01)
        await coordinator.async_write_bit("IPM1:M1", True)
        assert coordinator.get_bit("IPM1:M1") is True
        await asyncio.sleep(0.02)
        assert coordinator.get_bit("IPM1:M1") is False

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
        assert switch.is_on is False

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
                "IPM1:NA1.5": "1",
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
                "valve_marker": "IPM1:NA1.5",
                "temperature_scale": 0.5,
            },
        )
        assert climate.current_temperature == 20
        assert climate.target_temperature == 21
        assert climate.hvac_mode.value == "heat"
        assert climate.preset_modes == ["eco", "comfort"]
        assert climate.preset_mode == "eco"
        assert climate.hvac_action.value == "heating"
        await climate.async_set_preset_mode("comfort")
        assert coordinator.commands[-1] == ("bit", "IPM1:NE1.1", True)
        assert climate.preset_mode == "comfort"
        await climate.async_set_temperature(temperature=22.5)
        assert coordinator.commands[-1] == ("byte", "IPM1:M92", 45)
        assert ("IPM1:NE1.1", 1) in coordinator.optimistic_writes
        assert ("IPM1:M92", 45) in coordinator.optimistic_writes

    asyncio.run(run())


def test_climate_default_temperature_mapping_is_byte_plus_five() -> None:
    coordinator = FakeCoordinator({"IPM1:M220": "25", "IPM1:M221": "30"})
    climate = _entity(
        IsygltClimate,
        coordinator,
        {
            "id": "thermostat",
            "name": "Thermostat",
            "current_temp_marker": "IPM1:M220",
            "target_temp_marker": "IPM1:M221",
        },
    )
    assert climate.current_temperature == 25
    assert climate.target_temperature == 30
