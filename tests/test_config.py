from xdld.config import Config, load_config, parse_size
from xdld.runner import render_filename, safe_name


def test_render_template():
    out = render_filename(
        "{tweet_id}_{user}_{index}{ext}",
        {"tweet_id": "123", "user": "alice", "index": "0", "ext": ".mp4"},
    )
    assert out == "123_alice_0.mp4"


def test_render_unknown_placeholder_is_kept():
    try:
        render_filename("{tweet_id}_{nope}", {"tweet_id": "1"})
    except ValueError as exc:
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_safe_name_strips_path_separators():
    assert "/" not in safe_name("a/b")
    assert safe_name("") == "unknown"


def test_config_file_and_overrides(tmp_path):
    (tmp_path / "xdld.toml").write_text(
        'proxy = "http://127.0.0.1:1"\nmax_likes = 7\n', encoding="utf-8"
    )
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.proxy == "http://127.0.0.1:1"
    assert cfg.max_likes == 7

    cfg = load_config(
        None,
        base_dir=str(tmp_path),
        overrides={"proxy": "http://127.0.0.1:2"},
    )
    assert cfg.proxy == "http://127.0.0.1:2"


def test_config_relative_paths_become_absolute(tmp_path):
    cfg = load_config(
        None, base_dir=str(tmp_path), overrides={"cookies": "c.json", "db": "s.db"}
    )
    assert cfg.cookies == str((tmp_path / "c.json").resolve())
    assert cfg.db == str((tmp_path / "s.db").resolve())


def test_default_proxy():
    assert Config().proxy == "http://127.0.0.1:7897"


def test_config_paths_resolve_against_the_config_file_dir(tmp_path):
    """A relative path in xdld.toml belongs next to xdld.toml."""
    cfg_dir = tmp_path / "project"
    cfg_dir.mkdir()
    (cfg_dir / "xdld.toml").write_text('cookies = "cookies.txt"\n', encoding="utf-8")
    other = tmp_path / "elsewhere"
    other.mkdir()

    cfg = load_config(str(cfg_dir / "xdld.toml"), base_dir=str(other))
    assert cfg.cookies == str((cfg_dir / "cookies.txt").resolve())


def test_cli_paths_resolve_against_the_cwd(tmp_path):
    cfg = load_config(
        None, base_dir=str(tmp_path), overrides={"cookies": "sub/c.json"}
    )
    assert cfg.cookies == str((tmp_path / "sub" / "c.json").resolve())


def test_parse_size_understands_units():
    assert parse_size("50MB") == 50 * 1024 * 1024
    assert parse_size("50mb") == 50 * 1024 * 1024
    assert parse_size("1.5GB") == int(1.5 * 1024**3)
    assert parse_size("800K") == 800 * 1024
    assert parse_size("1 GB") == 1024**3
    assert parse_size(1234) == 1234
    assert parse_size("0") == 0


def test_parse_size_rejects_nonsense():
    for value in ("abc", "50 potatoes", ""):
        try:
            parse_size(value)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {value!r}")


def test_max_size_can_be_set_in_the_config_file(tmp_path):
    (tmp_path / "xdld.toml").write_text('max_size = "50MB"\n', encoding="utf-8")
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_size == 50 * 1024 * 1024


def test_max_size_can_be_set_in_bytes(tmp_path):
    (tmp_path / "xdld.toml").write_text("max_size = 12345\n", encoding="utf-8")
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_size == 12345


def test_max_size_can_be_set_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDLD_MAX_SIZE", "25MB")
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_size == 25 * 1024 * 1024


def test_the_environment_is_typed(tmp_path, monkeypatch):
    """Without the fix every env value stayed a string."""
    monkeypatch.setenv("XDLD_MAX_LIKES", "7")
    monkeypatch.setenv("XDLD_DOWNLOAD_IMAGES", "false")
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_likes == 7
    assert cfg.download_images is False


def test_the_command_line_wins_over_the_config_file(tmp_path):
    (tmp_path / "xdld.toml").write_text('max_size = "50MB"\n', encoding="utf-8")
    cfg = load_config(None, base_dir=str(tmp_path), overrides={"max_size": "10MB"})
    assert cfg.max_size == 10 * 1024 * 1024


def test_the_size_flags_can_be_set_in_the_config_file(tmp_path):
    (tmp_path / "xdld.toml").write_text(
        "max_size = \"50MB\"\nmax_size_strict = true\nmax_size_probe = false\n",
        encoding="utf-8",
    )
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_size == 50 * 1024 * 1024
    assert cfg.max_size_strict is True
    assert cfg.max_size_probe is False


def test_the_size_flags_can_be_set_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDLD_MAX_SIZE", "25MB")
    monkeypatch.setenv("XDLD_MAX_SIZE_STRICT", "1")
    monkeypatch.setenv("XDLD_MAX_SIZE_PROBE", "no")
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_size == 25 * 1024 * 1024
    assert cfg.max_size_strict is True
    assert cfg.max_size_probe is False


def test_the_size_flags_have_sane_defaults(tmp_path):
    cfg = load_config(None, base_dir=str(tmp_path))
    assert cfg.max_size == 0
    assert cfg.max_size_strict is False
    assert cfg.max_size_probe is True
