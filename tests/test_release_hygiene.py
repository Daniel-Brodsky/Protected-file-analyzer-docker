from pathlib import Path


def test_repo_contains_public_project_documents():
    root = Path(__file__).resolve().parents[1]
    for name in [
        'README.md',
        'SECURITY.md',
        'CONTRIBUTING.md',
        'CHANGELOG.md',
        'LICENSE',
        '.env.example',
        'compose.yaml',
        'install.sh',
        'install.ps1',
    ]:
        assert (root / name).exists(), name
