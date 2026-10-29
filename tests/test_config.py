import pytest

import config


def test_wrong_type_warns_and_keeps_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text("portapapeles = 1\n", encoding="utf-8")      # valid TOML, wrong shape
    assert config.load(p) == {"copy": "auto"}
    err = capsys.readouterr().err
    assert "config.toml" in err and "tabla" in err and "Traceback" not in err


def test_wrong_value_type_warns_and_keeps_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text("[portapapeles]\ncopiar = 3\n", encoding="utf-8")
    assert config.load(p) == {"copy": "auto"}
    assert "copiar debe ser texto" in capsys.readouterr().err


def test_non_utf8_file_warns_and_keeps_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_bytes('[portapapeles]\ncopiar = "nunca"\n# ñ\n'.encode("latin-1"))
    assert config.load(p) == {"copy": "auto"}
    err = capsys.readouterr().err
    assert "ilegible" in err and "valores por defecto" in err


def test_valid_config_is_silent(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('[portapapeles]\ncopiar = "Nunca"\n', encoding="utf-8")
    assert config.load(p) == {"copy": "nunca"}
    assert capsys.readouterr().err == ""


# ---------- primary language: config > system locale > es ----------
NO_LOCALE = {}


def _no_apple():
    return ""


def test_primary_language_from_config_wins_over_the_locale(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[idioma]\nprincipal = "EN"\n', encoding="utf-8")
    assert config.primary_language(p, env={"LANG": "es_CO.UTF-8"}, apple_locale=lambda: "es_CO") == "en"
    p.write_text('[idioma]\nprincipal = "es"\n', encoding="utf-8")
    assert config.primary_language(p, env={"LANG": "en_US.UTF-8"}, apple_locale=lambda: "en_US") == "es"


def test_primary_language_from_the_system_locale_without_config(tmp_path):
    missing = tmp_path / "no.toml"
    assert config.primary_language(missing, env={"LC_ALL": "en_GB.UTF-8", "LANG": "es_CO"}, apple_locale=_no_apple) == "en"
    assert config.primary_language(missing, env={"LANG": "es_CO.UTF-8"}, apple_locale=_no_apple) == "es"
    # LANG=C says nothing: macOS AppleLocale decides
    assert config.primary_language(missing, env={"LANG": "C"}, apple_locale=lambda: "en_US\n") == "en"
    assert config.primary_language(missing, env={}, apple_locale=lambda: "es_419") == "es"


def test_primary_language_falls_back_to_spanish(tmp_path):
    missing = tmp_path / "no.toml"
    assert config.primary_language(missing, env={"LANG": "fr_FR.UTF-8"}, apple_locale=lambda: "fr_FR") == "es"
    assert config.primary_language(missing, env={"LANG": "POSIX"}, apple_locale=_no_apple) == "es"

    def boom():
        raise TimeoutError("defaults colgado")

    assert config.primary_language(missing, env={}, apple_locale=boom) == "es"   # never an error


def test_invalid_primary_in_config_warns_and_uses_the_locale(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('[idioma]\nprincipal = "fr"\n', encoding="utf-8")
    assert config.primary_language(p, env={"LANG": "en_US.UTF-8"}, apple_locale=_no_apple) == "en"
    assert "[idioma] principal" in capsys.readouterr().err
    p.write_text("idioma = 3\n", encoding="utf-8")
    assert config.primary_language(p, env={}, apple_locale=_no_apple) == "es"
    assert "[idioma] principal" in capsys.readouterr().err


def test_locale_prefix_must_be_a_whole_language_code(tmp_path):
    missing = tmp_path / "no.toml"
    assert config.primary_language(missing, env={"LANG": "est_EE"}, apple_locale=lambda: "en_US") == "en"


def test_default_apple_locale_reader_uses_a_short_timeout_and_survives_failures(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(config.sys, "platform", "darwin")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="en_US\n", stderr="")

    monkeypatch.setattr(config.subprocess, "run", fake_run)
    missing = tmp_path / "no.toml"
    assert config.primary_language(missing, env={}) == "en"
    assert seen["cmd"][:3] == ["defaults", "read", "-g"] and seen["timeout"] <= 2

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(config.subprocess, "run", hang)
    assert config.primary_language(missing, env={}) == "es"


@pytest.mark.parametrize("lang, apple, expected", [
    ("C.UTF-8", "en_CO", "es"),      # the author's Mac: English menus, region Colombia
    ("en_US.UTF-8", "en_CO", "es"),  # an inherited en_US proves nothing: the region decides
    ("en_US.UTF-8", "en_US", "en"),
    ("C.UTF-8", "en_US", "en"),
    ("en_US.UTF-8", "", "en"),       # no AppleLocale to ask: the explicit en is all there is
    ("es_ES.UTF-8", "en_US", "es"),  # an explicit es_* wins
    ("es_MX.UTF-8", "", "es"),
    ("C.UTF-8", "en_GB", "en"),
    ("C.UTF-8", "fr_FR", "es"),
    ("C.UTF-8", "es_US", "es"),      # Spanish language wins even in a non-Spanish region
])
def test_primary_language_resolution_order(tmp_path, lang, apple, expected):
    got = config.primary_language(tmp_path / "no.toml", env={"LANG": lang}, apple_locale=lambda: apple)
    assert got == expected


def test_config_beats_a_spanish_region(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[idioma]\nprincipal = "en"\n', encoding="utf-8")
    assert config.primary_language(p, env={"LANG": "C"}, apple_locale=lambda: "en_CO") == "en"


def test_every_spanish_speaking_region_is_listed():
    assert "US" not in config.SPANISH_REGIONS
    assert {"AR", "CO", "ES", "MX", "GQ", "PR"} <= config.SPANISH_REGIONS
