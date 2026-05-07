from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.runtimes.workspace_env import (
    RuntimeExecOptions,
    create_in_memory_workspace_env,
    create_local_workspace_env,
    define_runtime_command,
)


def test_in_memory_workspace_normalizes_paths_and_files() -> None:
    env = create_in_memory_workspace_env(cwd="/project")

    env.write_file("src/app.py", "ANSWER = 42\n")

    assert env.resolve_path("src/app.py") == "/project/src/app.py"
    assert env.read_file("/project/src/app.py") == "ANSWER = 42\n"
    assert env.exists("src/app.py") is True
    assert env.exists("src/missing.py") is False
    assert env.readdir("src") == ["app.py"]

    file_stat = env.stat("src/app.py")
    assert file_stat.is_file is True
    assert file_stat.is_directory is False
    assert file_stat.size == len(b"ANSWER = 42\n")

    dir_stat = env.stat("src")
    assert dir_stat.is_directory is True


def test_in_memory_workspace_scopes_without_copying_filesystem() -> None:
    env = create_in_memory_workspace_env(cwd="/project")
    env.write_file("README.md", "root\n")

    scoped = env.scope(cwd="packages/core")
    scoped.write_file("README.md", "core\n")

    assert scoped.cwd == "/project/packages/core"
    assert scoped.read_file("README.md") == "core\n"
    assert env.read_file("README.md") == "root\n"
    assert env.read_file("packages/core/README.md") == "core\n"


def test_local_workspace_maps_file_operations_through_virtual_root(tmp_path: Path) -> None:
    env = create_local_workspace_env(root=tmp_path, cwd="/repo")

    env.write_file("src/index.py", "print('hello')\n")

    assert env.resolve_path("src/index.py") == "/repo/src/index.py"
    assert env.read_file("/repo/src/index.py") == "print('hello')\n"
    assert env.readdir("src") == ["index.py"]
    assert (tmp_path / "repo" / "src" / "index.py").read_text(encoding="utf-8") == "print('hello')\n"


def test_local_workspace_stats_and_removes_symlink_without_deleting_target(tmp_path: Path) -> None:
    env = create_local_workspace_env(root=tmp_path, cwd="/repo")
    env.mkdir("target", recursive=True)
    env.write_file("target/keep.txt", "safe\n")
    target = tmp_path / "repo" / "target"
    link = tmp_path / "repo" / "link"
    link.symlink_to(target, target_is_directory=True)

    link_stat = env.stat("link")
    assert link_stat.is_symbolic_link is True
    assert link_stat.is_directory is False

    env.rm("link", recursive=True)

    assert not link.exists()
    assert target.is_dir()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "safe\n"


def test_local_workspace_rejects_paths_that_escape_virtual_root(tmp_path: Path) -> None:
    env = create_local_workspace_env(root=tmp_path, cwd="/repo")
    outside_name = f"{tmp_path.name}-outside.txt"
    escape_path = f"../../{outside_name}"

    assert env.resolve_path(escape_path) == f"/{outside_name}"
    env.write_file(escape_path, "still inside the adapter root\n")

    assert (tmp_path / outside_name).read_text(encoding="utf-8") == "still inside the adapter root\n"
    assert not (tmp_path.parent / outside_name).exists()


def test_local_workspace_executes_commands_inside_virtual_cwd(tmp_path: Path) -> None:
    env = create_local_workspace_env(root=tmp_path, cwd="/repo")
    env.mkdir(".", recursive=True)

    result = env.exec("printf autoctx", options=RuntimeExecOptions(cwd="/repo"))

    assert result.stdout == "autoctx"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_scoped_command_grants_are_not_visible_to_parent() -> None:
    env = create_in_memory_workspace_env(cwd="/project")
    scoped = env.scope(
        commands=[
            define_runtime_command(
                "greet",
                lambda args, _context: {"stdout": f"hello {' '.join(args)}", "stderr": "", "exit_code": 0},
            )
        ]
    )

    assert scoped.exec("greet Ada Lovelace").stdout == "hello Ada Lovelace"
    assert env.exec("greet Ada").exit_code == 127


def test_in_memory_workspace_rejects_file_as_parent_directory() -> None:
    env = create_in_memory_workspace_env(cwd="/project")
    env.write_file("node", "file\n")

    with pytest.raises(NotADirectoryError, match="/project/node"):
        env.write_file("node/child.txt", "child\n")

    with pytest.raises(NotADirectoryError, match="/project/node"):
        env.mkdir("node/child", recursive=True)


def test_in_memory_workspace_rejects_file_directory_same_path_collision() -> None:
    env = create_in_memory_workspace_env(cwd="/project")
    env.mkdir("node", recursive=True)

    with pytest.raises(IsADirectoryError, match="/project/node"):
        env.write_file("node", "file\n")

    other = create_in_memory_workspace_env(cwd="/project")
    other.write_file("node", "file\n")

    with pytest.raises(FileExistsError, match="/project/node"):
        other.mkdir("node")


def test_command_grants_receive_trusted_env_and_virtual_cwd() -> None:
    env = create_in_memory_workspace_env(cwd="/project")
    scoped = env.scope(
        cwd="packages/core",
        commands=[
            define_runtime_command(
                "show-context",
                lambda _args, context: {
                    "stdout": f"{context.cwd}:{context.env.get('AUTOCTX_TOKEN', '')}",
                    "stderr": "",
                    "exit_code": 0,
                },
                env={"AUTOCTX_TOKEN": "trusted-secret"},
            )
        ],
    )

    result = scoped.exec("show-context", options=RuntimeExecOptions(env={"AUTOCTX_TOKEN": "prompt-value"}))

    assert result.stdout == "/project/packages/core:trusted-secret"


def test_cleanup_closes_in_memory_workspace() -> None:
    env = create_in_memory_workspace_env(cwd="/project")

    env.cleanup()

    try:
        env.read_file("README.md")
    except RuntimeError as exc:
        assert str(exc) == "Workspace environment has been cleaned up"
    else:
        raise AssertionError("Expected cleaned workspace to reject operations")
