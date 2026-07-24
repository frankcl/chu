"""Presentation theme API tests."""


class TestPptThemes:
    def test_returns_four_themes_with_colors(self, client):
        resp = client.get("/api/ppt/themes")
        assert resp.status_code == 200
        themes = resp.json()["themes"]
        assert [t["name"] for t in themes] == [
            "default", "business-blue", "tech-dark", "minimal"]
        tech = next(t for t in themes if t["name"] == "tech-dark")
        assert tech["colors"]["band"] == "2E6CB5"
        assert tech["label"]
        assert tech["sample"]["body"]

