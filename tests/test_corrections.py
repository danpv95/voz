import corrections


def rules_from(tmp_path, body):
    p = tmp_path / "corrections.toml"
    p.write_text(body, encoding="utf-8")
    return corrections.load(p)


def test_missing_file_gives_no_rules(tmp_path):
    assert corrections.load(tmp_path / "nope.toml") == []


def test_broken_toml_gives_no_rules(tmp_path, capsys):
    assert rules_from(tmp_path, "replacements = {") == []
    assert "ilegible" in capsys.readouterr().err


def test_short_keys_are_ignored(tmp_path, capsys):
    rules = rules_from(tmp_path, '[replacements]\n"ab" = "AB"\n"cloud" = "Claude"\n')
    assert len(rules) == 1
    assert "<3 chars" in capsys.readouterr().err


def test_case_only_rule_normalizes_every_variant(tmp_path):
    rules = rules_from(tmp_path, '[replacements]\n"nit" = "NIT"\n"okuli" = "Okuli"\n')
    assert len(rules) == 2
    assert corrections.apply("el nit de la empresa", rules) == "el NIT de la empresa"
    assert corrections.apply("Nit y nIt", rules) == "NIT y NIT"
    assert corrections.apply("habla okuli", rules) == "habla Okuli"


def test_case_only_rule_respects_word_boundaries(tmp_path):
    rules = rules_from(tmp_path, '[replacements]\n"nit" = "NIT"\n')
    assert corrections.apply("unidad nitido nit.", rules) == "unidad nitido NIT."


def test_case_only_rule_coexists_and_is_idempotent(tmp_path):
    rules = rules_from(tmp_path, '[replacements]\n"nit" = "NIT"\n"cloud" = "Claude"\n')
    once = corrections.apply("cloud y el nit", rules)
    assert once == "Claude y el NIT"
    assert corrections.apply(once, rules) == once


def test_longer_rule_wins(tmp_path):
    rules = rules_from(
        tmp_path, '[replacements]\n"cloud" = "Claude"\n"cloud code" = "Claude Code"\n'
    )
    assert corrections.apply("abre cloud code ya", rules) == "abre Claude Code ya"


def test_case_insensitive(tmp_path):
    rules = rules_from(tmp_path, '[replacements]\n"cloud" = "Claude"\n')
    assert corrections.apply("CLOUD y Cloud", rules) == "Claude y Claude"


def test_word_boundaries_with_accents(tmp_path):
    rules = rules_from(tmp_path, '[replacements]\n"cifra" = "Zifra"\n')
    assert corrections.apply("usa cifra", rules) == "usa Zifra"
    # must not touch longer words that merely contain the key
    assert corrections.apply("descifrado cifrado cifrá", rules) == "descifrado cifrado cifrá"


def test_example_config_does_not_mangle_legit_english():
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "config" / "corrections.example.toml"
    rules = corrections.load(example)
    assert corrections.apply("backup to the cloud", rules) == "backup to the cloud"
    assert corrections.apply("abre cloud code ya", rules) == "abre Claude Code ya"
    assert corrections.apply("usa vox li", rules) == "usa voz CLI"


def test_non_utf8_file_gives_no_rules_and_no_traceback(tmp_path, capsys):
    p = tmp_path / "corrections.toml"
    p.write_bytes('[replacements]\n"néqui" = "Nequi"\n'.encode("latin-1"))
    assert corrections.load(p) == []
    err = capsys.readouterr().err
    assert "ilegible" in err and "sigo sin correcciones" in err


def test_wrong_types_warn_and_skip(tmp_path, capsys):
    assert rules_from(tmp_path, "replacements = 1\n") == []
    assert "tabla" in capsys.readouterr().err
    rules = rules_from(tmp_path, '[replacements]\n"neki" = 5\n"neque" = "Nequi"\n')
    assert len(rules) == 1 and corrections.apply("usa neque", rules) == "usa Nequi"
    assert "'neki'" in capsys.readouterr().err
