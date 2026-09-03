from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from mainplate.config import KEYLESS
from mainplate.config import BadConfig
from mainplate.config import Config
from mainplate.config import Endpoint
from mainplate.config import parse_config
from mainplate.config import read_config
from mainplate.exe import Gateway
from mainplate.exe import parse_gateways

WHOLE = """
default: gateway
default_model: anthropic/claude-opus-5

endpoints:
  gateway:
    format: anthropic
    url: https://llm.int.exe.xyz

  wired:
    format: openai
    url: https://llm.int.exe.xyz/v1

  direct:
    format: anthropic
    api_key: sk-ant-secret
"""

# One endpoint and nothing optional, for the cases that are about a single rule.
MINIMAL = "default: here\nendpoints:\n  here:\n    format: anthropic\n"


class TestParsingAConfig:
    def test_a_whole_file_becomes_the_endpoints_it_declares(self) -> None:
        config = parse_config(WHOLE)
        assert config.default == "gateway"
        assert sorted(config.endpoints) == ["direct", "gateway", "wired"]
        assert config.endpoints["gateway"].url == "https://llm.int.exe.xyz"

    def test_an_endpoint_names_the_api_format_spoken_to_it(self) -> None:
        """One hostname answers both, so which is spoken is the endpoint's to say and not the host's."""
        config = parse_config(WHOLE)
        assert config.endpoints["gateway"].format == "anthropic"
        assert config.endpoints["wired"].format == "openai"

    def test_a_named_default_model_is_carried_through(self) -> None:
        assert parse_config(WHOLE).default_model == "anthropic/claude-opus-5"

    def test_a_file_that_names_no_default_model_leaves_it_to_the_endpoint(self) -> None:
        """Absent means "whatever it lists first", which is a question only discovery can answer."""
        assert parse_config(MINIMAL).default_model is None

    def test_a_file_naming_no_reference_looks_nothing_up(self) -> None:
        """The default, and what keeps a console from calling anybody its own file did not name."""
        assert parse_config(MINIMAL).model_reference is None

    def test_a_credential_is_held_redacted_rather_than_as_text(self) -> None:
        """A key that renders in a traceback or a log line is the whole failure this prevents."""
        key = parse_config(WHOLE).endpoints["direct"].api_key
        assert key is not None
        assert "sk-ant-secret" not in repr(key)
        assert key.get_secret_value() == "sk-ant-secret"

    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            ("default: [", "not YAML at all"),
            ("", "an empty file"),
            ("- a\n- list", "a document that is not a mapping"),
            ("default: nope\nendpoints:\n  here:\n    format: anthropic\n", "a default naming nothing"),
            ("default: here\n", "a default with no endpoints at all"),
            ("default: here\nendpoints:\n  here:\n    format: fireworks\n", "an API format nothing can speak"),
            ("default: here\nendpoints:\n  here:\n    api_key: k\n", "an endpoint naming no format at all"),
            (
                "default: here\nendpoints:\n  here:\n    format: anthropic\n    nonsense: 1\n",
                "a key nothing reads, which is usually a typo for one that is",
            ),
            (
                "default: here\nendpoints:\n  here:\n    format: anthropic\nmodel_reference:\n  source: x\n  format: nope\n",
                "a reference database in a shape nothing can read",
            ),
        ],
    )
    def test_what_cannot_run_is_refused_where_the_file_is_read(self, raw: str, why: str) -> None:
        with pytest.raises(BadConfig):
            parse_config(raw)

    def test_a_reference_naming_only_a_source_gets_the_one_format_there_is(self) -> None:
        """A file asking for the obvious thing should not have to spell out the only answer."""
        reference = parse_config(f"{MINIMAL}model_reference:\n  source: /models.json\n").model_reference
        assert reference is not None
        assert reference.format == "models.dev"


class TestChoosingACredential:
    def test_a_configured_key_is_what_the_sdk_gets(self) -> None:
        endpoint = Endpoint(format="anthropic", api_key=SecretStr("sk-mine"))
        assert endpoint.key == "sk-mine"

    def test_an_endpoint_with_no_key_gets_a_placeholder(self) -> None:
        """exe.dev injects the credential at its edge, and the SDK still refuses to construct bare."""
        endpoint = Endpoint(format="anthropic", url="https://llm.int.exe.xyz")
        assert endpoint.key == KEYLESS

    def test_neither_leaves_the_sdk_reading_the_environment_for_itself(self) -> None:
        """`None` is the value that keeps a plain `Agent('anthropic:...')` behaving as it always did."""
        assert Endpoint(format="anthropic").key is None


class TestDiscoveringAnExeGateway:
    def test_an_llm_integration_becomes_a_gateway_at_its_own_hostname(self) -> None:
        document = b'{"integrations": [{"name": "llm", "type": "llm"}]}'
        assert parse_gateways(document) == (Gateway(name="llm", base_url="https://llm.int.exe.xyz"),)

    def test_integrations_of_other_kinds_are_left_alone(self) -> None:
        document = b'{"integrations": [{"name": "atlas", "type": "github"}, {"name": "llm", "type": "llm"}]}'
        assert [found.name for found in parse_gateways(document)] == ["llm"]

    @pytest.mark.parametrize(
        ("document", "why"),
        [
            (b"not json", "a body that is not a document"),
            (b"[]", "a document that is not an object"),
            (b"{}", "a document with no integrations"),
            (b'{"integrations": "no"}', "integrations that are not a list"),
            (b'{"integrations": [{"type": "llm"}]}', "an integration with no name"),
            (b'{"integrations": [{"name": "", "type": "llm"}]}', "an integration named nothing"),
        ],
    )
    def test_anything_else_finds_nothing_rather_than_failing(self, document: bytes, why: str) -> None:
        """Reflection describes a VM this process merely happens to be on; it may say anything."""
        assert parse_gateways(document) == ()


class TestReadingAConfigFile:
    def test_a_missing_file_is_refused_naming_the_path(self, tmp_path: Path) -> None:
        """The ordinary first-run state, and a traceback through `pathlib` names nothing useful."""
        missing = tmp_path / "config.yaml"
        with pytest.raises(BadConfig, match=str(missing)):
            read_config(missing)

    def test_a_malformed_file_is_refused_naming_the_path_too(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("default = [")
        with pytest.raises(BadConfig, match=str(path)):
            read_config(path)


class TestBuildingAConfigInCode:
    def test_a_config_with_no_profiles_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="no endpoints"):
            Config(default="here", endpoints={})
