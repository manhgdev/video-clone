from types import SimpleNamespace

from pipeline.core.system_check import checks, install


def _ok(*_args, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def test_install_nvm_windows_installs_node_lts(monkeypatch, tmp_path) -> None:
    nvm_home = tmp_path / "nvm"
    nvm_home.mkdir()
    nvm_exe = nvm_home / "nvm.exe"
    nvm_exe.touch()
    calls = []

    monkeypatch.setattr(install.sys, "platform", "win32")
    monkeypatch.setenv("NVM_HOME", str(nvm_home))
    monkeypatch.setattr(install.shutil, "which", lambda name: "winget.exe" if name == "winget" else None)
    monkeypatch.setattr(install, "_pip_stream", lambda args, **kwargs: calls.append(args) or _ok())

    result = install.install_nvm()

    assert result["ok"] is True
    assert calls[0][:4] == ["winget.exe", "install", "--id", "CoreyButler.NVMforWindows"]
    assert calls[1:] == [[str(nvm_exe), "install", "lts"], [str(nvm_exe), "use", "lts"]]


def test_install_nvm_posix_installs_node_lts(monkeypatch, tmp_path) -> None:
    nvm_dir = tmp_path / ".nvm"
    nvm_dir.mkdir()
    (nvm_dir / "nvm.sh").touch()
    calls = []

    monkeypatch.setattr(install.sys, "platform", "darwin")
    monkeypatch.setenv("NVM_DIR", str(nvm_dir))
    monkeypatch.setattr(install.shutil, "which", lambda _name: None)
    monkeypatch.setattr(install.urllib.request, "urlopen", lambda *_args, **_kwargs: SimpleNamespace(read=lambda: b"exit 0"))
    monkeypatch.setattr(install, "_pip_stream", lambda args, **kwargs: calls.append(args) or _ok())

    result = install.install_nvm()

    assert result["ok"] is True
    assert calls[0][0] == "bash"
    assert calls[1][:2] == ["bash", "-lc"]
    assert "nvm install --lts" in calls[1][2]


def test_node_executable_finds_nvm_install_without_updated_path(monkeypatch, tmp_path) -> None:
    node = tmp_path / "versions/node/v22/bin/node"
    node.parent.mkdir(parents=True)
    node.touch()

    monkeypatch.setattr(checks.sys, "platform", "darwin")
    monkeypatch.setenv("NVM_DIR", str(tmp_path))
    monkeypatch.setattr(checks, "_which", lambda _name: None)

    assert checks._node_executable() == str(node)
