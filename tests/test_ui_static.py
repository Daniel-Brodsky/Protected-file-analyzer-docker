from pathlib import Path

from fastapi.testclient import TestClient

from protected_file_analyzer.app import create_app


def test_index_exposes_single_analyze_action_generic_progress_and_cancel(app_env):
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<html lang="he" dir="rtl">' in html
    assert 'ניתוח סטטי מאובטח' in html
    assert 'שער ניתוח סטטי' not in html
    assert 'הכלי נועד לנתח סטטית קבצים מוגנים בסיסמא, תוך שמירה על אבטחת המידע.' in html
    assert 'התחלת ניתוח' in html
    assert 'Preparing' in html
    assert 'Recovering access' in html
    assert 'Decrypting' in html
    assert 'Static analysis' in html
    assert 'Completed' in html
    assert 'name="custom_wordlist"' not in html
    assert 'אם יועלה, הוא ישמש בתוך מדיניות שחזור הגישה הקבועה של המערכת.' not in html
    assert 'עד 100MB כברירת מחדל' not in html
    assert 'השירות מטפל בזיהוי פורמט, במדיניות שחזור גישה, בפענוח ובניתוח סטטי בתוך המערכת.' not in html
    assert 'id="cancel-job"' in html
    assert 'value="pin4"' not in html
    assert 'value="israeli_id"' not in html
    assert 'value="rockyou"' not in html
    assert 'value="mounted"' not in html
    assert 'name="wordlist_mode"' not in html


def test_index_uses_versioned_static_assets(app_env):
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '/styles.css?v=' in html
    assert '/app.js?v=' in html


def test_static_assets_render_safe_tool_cards_without_unsafe_tool_html():
    static_dir = Path(__file__).resolve().parents[1] / 'src' / 'protected_file_analyzer' / 'static'
    script = (static_dir / 'app.js').read_text(encoding='utf-8')
    styles = (static_dir / 'styles.css').read_text(encoding='utf-8')

    assert 'tool-card' in script
    assert 'פלט כלי' in script
    assert 'ממצאים מפוענחים' in script
    assert 'הורדת פלט גולמי' in script
    assert 'innerHTML = (report.tool_cards' not in script
    assert 'textContent = JSON.stringify(report, null, 2)' in script
    assert 'async function readResponsePayload(response)' in script
    assert 'response.headers.get(\'content-type\')' in script
    assert 'customWordlist.value' not in script
    assert "{ label: 'Bytes'" in script
    assert 'המערכת לא הצליחה למצוא סיסמה מתאימה לקובץ באמצעות מקורות השחזור הזמינים.' in script
    assert 'המערכת לא הצליחה למצוא סיסמה מתאימה לקובץ במסגרת המגבלות שהוגדרו.' in script
    assert '.tool-card' in styles
    assert '.tab-list' in styles
    assert '.tool-output-note' in styles
    assert '.tech-ltr' in styles
    assert 'overflow-wrap: anywhere' in styles or 'word-break: break-word' in styles
