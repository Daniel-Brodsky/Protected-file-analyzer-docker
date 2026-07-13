from pathlib import Path


def test_ensure_runtime_dirs_uses_pfa_runtime_gid_contract():
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'ensure-runtime-dirs.sh').read_text(encoding='utf-8')

    assert 'PFA_RUNTIME_GID' in script
    assert 'HOST_GID="${PFA_RUNTIME_GID:-$(id -g)}"' in script
