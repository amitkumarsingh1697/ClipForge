def test_blank_env_lines_use_defaults(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("TELEGRAM_OWNER_CHAT_ID=\nCLIPS_PER_SOURCE=\nAPI_TOKEN=\n")
    from app.config import Settings
    s = Settings()
    assert s.telegram_owner_chat_id == 0 and s.clips_per_source == 3
