"""PWA publication contracts use fixtures, never replacement market data."""

import json
import re
import struct
from copy import deepcopy
from urllib.parse import urljoin

from taiwan_stock_ai.site import render_site


def publication(path):
    return json.loads((path / "assets/publication.json").read_text())


def test_pwa_install_assets_are_relative_to_the_project_scope(tmp_path):
    render_site({"as_of": "2026-10-28"}, tmp_path)
    manifest = json.loads((tmp_path / "manifest.webmanifest").read_text())
    base = "https://example.org/taiwan-stock-ai/"
    assert urljoin(base, manifest["scope"]) == base
    assert urljoin(base, manifest["start_url"]) == base + "index.html"
    assert urljoin(base, manifest["id"]) == base
    assert manifest["display"] == "standalone"
    assert manifest["lang"].startswith("zh")
    assert {urljoin(base, s["url"]) for s in manifest["shortcuts"]} >= {
        base + "preopen.html", base + "postclose.html",
    }
    for icon in manifest["icons"]:
        filename = icon["src"].removeprefix("./")
        data = (tmp_path / filename).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert icon["sizes"] == f"{width}x{height}"
        assert width == height and width >= 192
    assert any("maskable" in icon.get("purpose", "") for icon in manifest["icons"])
    worker = (tmp_path / "sw.js").read_text()
    assert "__PWA_VERSION__" not in worker
    assert publication(tmp_path)["app_version"] in worker
    assert (tmp_path / "offline.html").is_file()
    assert (tmp_path / "assets/pwa.js").is_file()
    for page in ("index", "preopen", "postclose", "final"):
        html = (tmp_path / f"{page}.html").read_text()
        assert 'rel="manifest" href="manifest.webmanifest"' in html
        assert 'worker-src \'self\'' in html
        assert f'data-page="{page}"' in html
        assert f'data-page-revision="{publication(tmp_path)["pages"][page]["revision"]}"' in html


def test_report_updates_preserve_unrelated_page_and_worker_version(tmp_path):
    preopen = {"as_of": "2026-10-28", "phase": "preopen", "data_date": "2026-10-27",
               "generated_at": "2026-10-28T00:20:00Z", "status": "partial"}
    postclose = {"as_of": "2026-10-28", "phase": "postclose", "data_date": "2026-10-28",
                 "generated_at": "2026-10-28T07:30:00Z", "status": "partial"}
    render_site(postclose, tmp_path, {"preopen": preopen})
    first = publication(tmp_path)
    preopen_html = (tmp_path / "preopen.html").read_bytes()
    worker = (tmp_path / "sw.js").read_bytes()
    changed = {**postclose, "status": "ready", "generated_at": "2026-10-28T08:00:00Z"}
    render_site(changed, tmp_path, {"preopen": preopen})
    second = publication(tmp_path)
    assert first["pages"]["preopen"] == second["pages"]["preopen"]
    assert preopen_html == (tmp_path / "preopen.html").read_bytes()
    assert first["pages"]["postclose"]["revision"] != second["pages"]["postclose"]["revision"]
    assert first["app_version"] == second["app_version"]
    assert worker == (tmp_path / "sw.js").read_bytes()
    render_site(changed, tmp_path, {"preopen": preopen})
    assert second == publication(tmp_path), "Identical reports must not look newly published on every render"


def test_publication_preserves_data_dates_and_missing_phase_status(tmp_path):
    report = {"as_of": "2026-10-28", "phase": "postclose", "data_date": "2026-10-27",
              "status": "partial", "generated_at": "2026-10-28T07:30:00Z"}
    original = deepcopy(report)
    render_site(report, tmp_path)
    data = publication(tmp_path)
    assert report == original
    assert data["schema_version"] == 1
    for page, entry in data["pages"].items():
        saved = json.loads((tmp_path / "assets" / ("report.json" if page == "index" else page + ".json")).read_text())
        assert re.fullmatch(r"[0-9a-f]{64}", entry["revision"])
        assert set(entry) == {"revision", "phase", "as_of", "data_date", "generated_at", "status"}
        for key in ("phase", "as_of", "data_date", "generated_at", "status"):
            assert entry[key] == saved.get(key)
    assert data["pages"]["preopen"]["status"] == "waiting"
    assert data["pages"]["preopen"]["data_date"] is None
    assert data["pages"]["preopen"]["generated_at"] is None


def test_interface_release_invalidates_page_revision_without_changing_report(tmp_path, monkeypatch):
    report = {"as_of": "2026-10-28", "phase": "preopen", "data_date": "2026-10-27",
              "generated_at": "2026-10-28T00:20:00Z"}
    render_site(report, tmp_path)
    before = publication(tmp_path)
    saved_report = (tmp_path / "assets/preopen.json").read_bytes()
    monkeypatch.setattr("taiwan_stock_ai.site.get_plotlyjs", lambda: "/* interface release fixture */")
    render_site(report, tmp_path)
    after = publication(tmp_path)
    assert before["app_version"] != after["app_version"]
    assert before["pages"]["preopen"]["revision"] != after["pages"]["preopen"]["revision"]
    assert saved_report == (tmp_path / "assets/preopen.json").read_bytes()
