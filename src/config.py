"""User settings from ~/.config/voz/config.toml (all optional).

    [portapapeles]
    copiar = "auto"        # auto (default) | preguntar | nunca

    [idioma]
    principal = "es"       # es | en: idioma al que se inclina --lang auto (default: se deduce del sistema)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

PATH = Path(os.environ.get("VOZ_CONFIG", Path.home() / ".config" / "voz" / "config.toml"))
COPY_MODES = ("auto", "preguntar", "nunca")
PRIMARY_LANGUAGES = ("es", "en")
FALLBACK_LANGUAGE = "es"
# Regions where the user speaks Spanish whatever language the OS menus use (US excluded).
SPANISH_REGIONS = frozenset("AR BO CL CO CR CU DO EC ES GT GQ HN MX NI PA PE PR PY SV UY VE".split())


def _warn(msg: str) -> None:
    print(f"⚠  config.toml: {msg}; uso valores por defecto.", file=sys.stderr)


def load(path: Path | None = None) -> dict:
    p = path or PATH
    data: dict = {}
    if p.exists():
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
            _warn(f"ilegible ({e})")
    section = data.get("portapapeles", {})
    if not isinstance(section, dict):
        _warn("[portapapeles] debe ser una tabla")
        section = {}
    copy = section.get("copiar", "auto")
    if not isinstance(copy, str):
        _warn("copiar debe ser texto (auto, preguntar o nunca)")
        copy = "auto"
    copy = copy.lower()
    return {"copy": copy if copy in COPY_MODES else "auto"}


def _from_locale(value: str) -> str | None:
    """'es_CO.UTF-8' -> 'es', 'en-US' -> 'en'; 'C', 'POSIX', 'et_EE' and '' -> None."""
    v = value.strip().lower()
    if v[:2] in PRIMARY_LANGUAGES and (len(v) == 2 or v[2] in "_.-@"):
        return v[:2]
    return None


def _region(value: str) -> str:
    """'en_CO' -> 'CO', 'es_419' -> '419', 'en_US@calendar=gregorian' -> 'US', 'en' -> ''."""
    v = value.strip().split("@", 1)[0].split(".", 1)[0].replace("-", "_")
    return v.partition("_")[2].upper()


def _apple_locale() -> str:
    """`defaults read -g AppleLocale` (macOS only), with a short timeout; '' if unavailable."""
    if sys.platform != "darwin":
        return ""
    out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True,
                         text=True, timeout=1.0, check=False)
    return out.stdout


def primary_language(path: Path | None = None, env: Mapping[str, str] | None = None,
                     apple_locale: Callable[[], str] | None = None) -> str:
    """Language `--lang auto` leans towards ("es" | "en"), first match wins:
    1. `[idioma] principal` in config.toml (an invalid value warns and is skipped);
    2. LC_ALL / LANG, only when it says Spanish (`es_*`). An `en_*` there proves nothing
       (a terminal inherits en_US very often), so it only counts if step 3 has no answer;
    3. macOS AppleLocale: a Spanish-speaking REGION wins (`en_CO` = English menus in
       Colombia, and the user speaks Spanish), otherwise the locale's own language
       if it is es or en;
    4. the `en` seen in step 2, if any; else "es"."""
    p = path or PATH
    try:
        section = tomllib.loads(p.read_text(encoding="utf-8")).get("idioma")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        section = None                     # load() already reports an unreadable file
    if section is not None:
        value = section.get("principal") if isinstance(section, dict) else None
        if isinstance(value, str) and value.lower() in PRIMARY_LANGUAGES:
            return value.lower()
        _warn('[idioma] principal debe ser "es" o "en"')
    env = os.environ if env is None else env
    from_env = None
    for var in ("LC_ALL", "LANG"):
        from_env = _from_locale(env.get(var, ""))
        if from_env:
            break
    if from_env == "es":
        return "es"
    try:
        apple = (apple_locale or _apple_locale)()
    except Exception:  # noqa: BLE001 - no defaults binary, timeout, odd output: not an error
        apple = ""
    if _region(apple) in SPANISH_REGIONS:
        return "es"
    return _from_locale(apple) or from_env or FALLBACK_LANGUAGE
