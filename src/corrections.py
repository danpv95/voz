"""Offline proper-noun / tech-term corrections applied after transcription.

Whisper mangles proper nouns it hasn't heard in Spanish ("Claude" -> "Cloud",
"voz cli" -> "voz li"). A biasing prompt fixes that but gets regurgitated on
silence, so instead we post-process with a small user-editable dictionary.

Rules file: ~/.config/voz/corrections.toml  (seed: config/corrections.example.toml)
Only put proper nouns / tech terms here - never common word pairs like
poner/poder, which are acoustic errors and would cause false positives.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "voz" / "corrections.toml"

Rule = tuple[re.Pattern, str]


def load(path: Path | None = None) -> list[Rule]:
    """Load correction rules. Missing/malformed file -> [] (never breaks transcription)."""
    p = path or DEFAULT_PATH
    if not p.exists():
        return []
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"⚠  corrections.toml ilegible ({e}); sigo sin correcciones.", file=sys.stderr)
        return []

    replacements = data.get("replacements", {})
    if not isinstance(replacements, dict):
        print("⚠  corrections.toml: [replacements] debe ser una tabla; sigo sin correcciones.",
              file=sys.stderr)
        return []
    # Longer keys first so multi-word rules win ("cloud code" before "cloud").
    rules: list[Rule] = []
    for key, value in sorted(replacements.items(), key=lambda kv: len(kv[0]), reverse=True):
        if not isinstance(value, str):
            print(f"⚠  regla '{key}': el valor debe ser texto, ignorada.", file=sys.stderr)
            continue
        if len(key) < 3:
            print(f"⚠  regla '{key}' <3 chars, ignorada (riesgo de falsos positivos).",
                  file=sys.stderr)
            continue
        # A rule that only changes case ("nit" = "NIT") is valid: it normalizes every
        # case variant of the word to `value`.
        # (?<!\w)...(?!\w): word boundaries that also work when the key starts/ends
        # in a non-word char. Python re is Unicode, so á/ñ count as \w.
        pattern = re.compile(rf"(?<!\w){re.escape(key)}(?!\w)", re.IGNORECASE)
        rules.append((pattern, value))
    return rules


def _combined(rules: list[Rule]) -> re.Pattern:
    key = tuple(p.pattern for p, _ in rules)
    pat = _COMBINED.get(key)
    if pat is None:
        # rules come longest-first, so at each position the alternation tries the
        # multi-word key before its shorter parts
        pat = re.compile("|".join(f"(?:{k})" for k in key), re.IGNORECASE)
        _COMBINED.clear()
        _COMBINED[key] = pat
    return pat


_COMBINED: dict[tuple, re.Pattern] = {}


def apply(text: str, rules: list[Rule]) -> str:
    """One pass: the output of a rule is never re-matched by another rule
    ("queso campesino" → "… La Vaca" must not then trigger the "la vaca" rule)."""
    if not rules:
        return text

    def pick(m: re.Match) -> str:
        for pattern, value in rules:
            if pattern.fullmatch(m.group()):
                return value                     # literal: "C:\\dev" is not a regex escape
        return m.group()

    return _combined(rules).sub(pick, text)
