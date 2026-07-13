from pathlib import Path

from fastapi.testclient import TestClient

from protected_file_analyzer.app import create_app


def test_index_exposes_wordlist_options_and_flight_progress_ui(app_env):
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'value="pin4"' in html
    assert 'value="israeli_id"' in html
    assert 'value="mounted"' in html
    assert 'קודם PIN בן 4 ספרות' in html
    assert 'id="flight-progress"' in html
    assert 'id="flight-path"' in html
    assert 'id="flight-plane"' in html
    assert 'id="stage-progress"' in html


def test_index_uses_versioned_static_assets(app_env):
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '/styles.css?v=' in html
    assert '/app.js?v=' in html


def test_static_assets_render_summary_error_hooks():
    static_dir = Path(__file__).resolve().parents[1] / 'src' / 'protected_file_analyzer' / 'static'
    script = (static_dir / 'app.js').read_text(encoding='utf-8')
    styles = (static_dir / 'styles.css').read_text(encoding='utf-8')

    assert 'summary-card' in script
    assert 'summary-label' in script
    assert 'summary-value' in script
    assert 'לא בוצעה סריקה סטטית' in script
    assert '.badge.error' in styles
    assert '.status-message.error' in styles
    assert 'overflow-wrap: anywhere' in styles or 'word-break: break-word' in styles
