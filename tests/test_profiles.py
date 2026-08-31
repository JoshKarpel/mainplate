from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from mainplate.exe import Gateway
from mainplate.exe import parse_gateways
from mainplate.profiles import KEYLESS
from mainplate.profiles import BadConfig
from mainplate.profiles import Config
from mainplate.profiles import Profile
from mainplate.profiles import parse_config
from mainplate.profiles import read_config

WHOLE = """
default = "gateway"

[profiles.gateway]
provider = "anthropic"
base_url = "https://llm.int.exe.xyz"
models = ["claude-sonnet-4-6", "claude-opus-4-1"]

[profiles.direct]
provider = "anthropic"
api_key = "sk-ant-secret"
models = ["claude-sonnet-5"]
"""


class TestParsingAConfig:
    def test_a_whole_file_becomes_the_profiles_it_declares(self) -> None:
        config = parse_config(WHOLE)
        assert config.default == "gateway"
        assert sorted(config.profiles) == ["direct", "gateway"]
        assert config.profiles["gateway"].models == ("claude-sonnet-4-6", "claude-opus-4-1")

    def test_the_default_model_is_the_default_profiles_first(self) -> None:
        assert parse_config(WHOLE).default_model == "claude-sonnet-4-6"

    def test_a_credential_is_held_redacted_rather_than_as_text(self) -> None:
        """A key that renders in a traceback or a log line is the whole failure this prevents."""
        key = parse_config(WHOLE).profiles["direct"].api_key
        assert key is not None
        assert "sk-ant-secret" not in repr(key)
        assert key.get_secret_value() == "sk-ant-secret"

    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            ("default = [", "not TOML at all"),
            ('default = "nope"\n[profiles.here]\nprovider="anthropic"\nmodels=["m"]', "a default naming nothing"),
            ('default = "here"', "a default with no profiles at all"),
            ('default = "here"\n[profiles.here]\nprovider="anthropic"\nmodels=[]', "a profile offering no model"),
            ('default = "here"\n[profiles.here]\nprovider="openai"\nmodels=["m"]', "a provider nothing can build"),
            ('default = "here"\n[profiles.here]\nprovider="anthropic"', "a profile with no models key"),
            (
                'default = "here"\n[profiles.here]\nprovider="anthropic"\nmodels=["m"]\nnonsense=1',
                "a key nothing reads, which is usually a typo for one that is",
            ),
        ],
    )
    def test_what_cannot_run_is_refused_where_the_file_is_read(self, raw: str, why: str) -> None:
        with pytest.raises(BadConfig):
            parse_config(raw)


class TestChoosingACredential:
    def test_a_configured_key_is_what_the_sdk_gets(self) -> None:
        profile = Profile(provider="anthropic", api_key=SecretStr("sk-mine"), models=("m",))
        assert profile.key == "sk-mine"

    def test_an_endpoint_with_no_key_gets_a_placeholder(self) -> None:
        """exe.dev injects the credential at its edge, and the SDK still refuses to construct bare."""
        profile = Profile(provider="anthropic", base_url="https://llm.int.exe.xyz", models=("m",))
        assert profile.key == KEYLESS

    def test_neither_leaves_the_sdk_reading_the_environment_for_itself(self) -> None:
        """`None` is the value that keeps a plain `Agent('anthropic:...')` behaving as it always did."""
        assert Profile(provider="anthropic", models=("m",)).key is None


class TestOfferings:
    def test_a_configured_pair_is_offered(self) -> None:
        assert parse_config(WHOLE).offers("gateway", "claude-opus-4-1") is True

    @pytest.mark.parametrize(
        ("profile", "model"),
        [("gateway", "claude-sonnet-5"), ("direct", "claude-opus-4-1"), ("gone", "claude-sonnet-5")],
    )
    def test_a_pair_from_another_profile_or_no_profile_is_not(self, profile: str, model: str) -> None:
        """A session records a pair, so what is checked later is the pair and never the halves."""
        assert parse_config(WHOLE).offers(profile, model) is False


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
        missing = tmp_path / "config.toml"
        with pytest.raises(BadConfig, match=str(missing)):
            read_config(missing)

    def test_a_malformed_file_is_refused_naming_the_path_too(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("default = [")
        with pytest.raises(BadConfig, match=str(path)):
            read_config(path)


class TestBuildingAConfigInCode:
    def test_a_config_with_no_profiles_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="no profiles"):
            Config(default="here", profiles={})
