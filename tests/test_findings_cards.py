from __future__ import annotations

import json
from pathlib import Path

from protected_file_analyzer.tool_worker import parse_olevba_output, parse_pdfid_output, scan


def test_parse_pdfid_output_extracts_expected_counters():
    sample = """
PDFiD 0.2.8 sample.pdf
 PDF Header: %PDF-1.7
 obj                    18
 endobj                 18
 stream                  3
 endstream               3
 xref                    1
 trailer                 1
 startxref               1
 /Page                   2
 /Encrypt                1
 /ObjStm                 1
 /JS                     2
 /JavaScript             1
 /AA                     0
 /OpenAction             1
 /Launch                 0
 /EmbeddedFile           0
 /AcroForm               1
 /XFA                    0
 /RichMedia              0
 /JBIG2Decode            0
""".strip()

    parsed = parse_pdfid_output(sample)

    assert parsed["counters"]["/JS"] == 2
    assert parsed["counters"]["/JavaScript"] == 1
    assert parsed["counters"]["/OpenAction"] == 1
    assert parsed["counters"]["/ObjStm"] == 1
    assert parsed["counters"]["/JBIG2Decode"] == 0


def test_parse_olevba_output_preserves_familiar_columns():
    sample = """
+----------+--------------------+---------------------------------------------+
| Type     | Keyword            | Description                                 |
+----------+--------------------+---------------------------------------------+
| AutoExec | AutoOpen           | Runs when the Word document is opened       |
| Suspicious | Shell            | May run an executable file or system command |
| IOC      | http://bad.test/a  | URL                                         |
+----------+--------------------+---------------------------------------------+
""".strip()

    parsed = parse_olevba_output(sample)

    assert parsed["rows"][0]["Type"] == "AutoExec"
    assert parsed["rows"][0]["Keyword"] == "AutoOpen"
    assert parsed["rows"][1]["Keyword"] == "Shell"
    assert any(row["Type"] == "IOC" for row in parsed["rows"])


def test_scan_stores_safe_truncated_pdfid_and_olevba_cards(monkeypatch, tmp_path: Path):
    target = tmp_path / 'scan-target'
    target.mkdir()
    pdf_file = target / '<svg onload=1>.pdf'
    office_file = target / 'sample<script>.docm'
    pdf_file.write_bytes(b'%PDF-1.7\n')
    office_file.write_bytes(b'PK\x03\x04')
    report_path = tmp_path / 'report.json'
    artifact_path = tmp_path / 'artifact.bin'
    rules = tmp_path / 'rules.yar'
    rules.write_text('rule always_true { condition: true }\n', encoding='utf-8')

    long_pdfid = '\x1b[31mPDFiD 0.2.8 payload.pdf\x1b[0m\n/JS 2\n' + ('A' * 300)
    long_olevba = '| Type | Keyword | Description |\n| AutoExec | <script> | bad |\n' + ('B' * 300)
    outputs = {
        ('pdfid.py', str(pdf_file)): {
            'available': True,
            'tool_name': 'PDFiD',
            'tool_version': '0.2.8',
            'exit_status': 0,
            'raw_stdout': long_pdfid,
            'raw_stderr': '',
        },
        ('olevba', '--analysis', str(office_file)): {
            'available': True,
            'tool_name': 'olevba',
            'tool_version': '0.60.1',
            'exit_status': 0,
            'raw_stdout': long_olevba,
            'raw_stderr': '',
        },
    }

    def fake_optional_tool(command, timeout=90, **kwargs):
        key = tuple(command)
        if key in outputs:
            return outputs[key]
        return {
            'available': False,
            'tool_name': command[0],
            'tool_version': None,
            'exit_status': None,
            'raw_stdout': '',
            'raw_stderr': '',
        }

    monkeypatch.setenv('PFA_TOOL_OUTPUT_UI_MAX_BYTES', '80')
    monkeypatch.setenv('PFA_TOOL_OUTPUT_DOWNLOAD_MAX_BYTES', '160')
    monkeypatch.setattr('protected_file_analyzer.tool_worker.optional_tool', fake_optional_tool)
    monkeypatch.setattr('protected_file_analyzer.tool_worker.shutil.which', lambda name: 'pdfid.py' if name in {'pdfid.py', 'pdfid'} else name)

    result = scan(target, report_path, artifact_path, rules, max_files=100, max_bytes=1024 * 1024)
    report = json.loads(report_path.read_text(encoding='utf-8'))

    assert result['ok'] is True
    assert artifact_path.exists()
    assert report['tool_cards']
    pdfid_card = next(card for card in report['tool_cards'] if card['tool'] == 'PDFiD')
    olevba_card = next(card for card in report['tool_cards'] if card['tool'] == 'olevba')
    assert '\x1b' not in pdfid_card['raw_stdout']
    assert pdfid_card['raw_stdout_truncated'] is True
    assert pdfid_card['raw_output_download']
    assert (report_path.parent / pdfid_card['raw_output_download']).exists()
    assert olevba_card['parsed_findings']['rows'][0]['Type'] == 'AutoExec'
    assert '<script>' in olevba_card['parsed_findings']['rows'][0]['Keyword']
    assert olevba_card['raw_stdout_truncated'] is True
