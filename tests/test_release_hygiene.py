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


def test_compose_defaults_to_prebuilt_images():
    compose = (Path(__file__).resolve().parents[1] / 'compose.yaml').read_text(encoding='utf-8')

    assert 'image:' in compose
    assert 'ghcr.io/daniel-brodsky/protected-file-analyzer-docker-web' in compose
    assert 'ghcr.io/daniel-brodsky/protected-file-analyzer-docker-worker' in compose
    assert 'docker compose pull' in (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
