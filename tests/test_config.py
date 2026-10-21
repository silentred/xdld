from xdld.config import Config, load_config
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
