"""load_env_file: a real environment variable wins, and only names come back."""

import settings


def test_load_env_file_keeps_real_variables_and_returns_names(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP_ME=from-file\nNEW_ONE=secret-value\n", encoding="utf-8")
    monkeypatch.setenv("KEEP_ME", "from-environment")
    monkeypatch.delenv("NEW_ONE", raising=False)
    names = settings.load_env_file(env_file)
    assert names == ["NEW_ONE"]
    import os
    assert os.environ["KEEP_ME"] == "from-environment"
    assert os.environ["NEW_ONE"] == "secret-value"
    monkeypatch.delenv("NEW_ONE")


def test_load_env_file_without_a_file_loads_nothing(tmp_path):
    assert settings.load_env_file(tmp_path / "missing.env") == []
