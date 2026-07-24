"""Generated file API tests."""

from unittest.mock import patch


class TestDownloadFile:
    def test_serves_existing_file(self, client, tmp_path):
        deck = tmp_path / "deck.pptx"
        deck.write_bytes(b"PK\x03\x04 fake pptx")
        with patch("web_api.files.GENERATED_DIR", tmp_path):
            resp = client.get("/api/files/deck.pptx")
        assert resp.status_code == 200
        assert resp.content == b"PK\x03\x04 fake pptx"

    def test_missing_file_returns_404(self, client, tmp_path):
        with patch("web_api.files.GENERATED_DIR", tmp_path):
            resp = client.get("/api/files/nope.pptx")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client, tmp_path):
        (tmp_path / "deck.pptx").write_bytes(b"x")
        with patch("web_api.files.GENERATED_DIR", tmp_path):
            resp = client.get("/api/files/..%2f..%2fserver.py")
        assert resp.status_code == 404

