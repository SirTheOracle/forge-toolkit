"""Repository-config guard for the copied operator's pinned Git calls."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


class Refusal(ValueError):
    pass


COMMANDS = {"add", "branch", "cat-file", "check-attr", "check-ignore", "check-ref-format", "commit-tree",
            "config", "diff", "diff-tree", "fetch", "log", "ls-files", "merge", "merge-base", "remote",
            "rev-list", "rev-parse", "show", "show-ref", "status", "symbolic-ref", "update-ref", "worktree", "write-tree"}
OVERRIDES = ("core.hooksPath=/dev/null", "core.fsmonitor=false", "core.pager=cat", "pager.branch=false",
             "commit.gpgSign=false", "tag.gpgSign=false", "credential.helper=", "gc.auto=0", "maintenance.auto=false",
             "submodule.recurse=false", "fetch.recurseSubmodules=false")
FALSE = {"false", "no", "off", "0"}


def regular_bytes(path, limit=1024 * 1024):
    if path.parent.resolve(strict=True) != path.parent:
        raise Refusal("GIT_CONFIG_ALIAS: " + str(path.parent))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise Refusal("GIT_CONFIG_UNSAFE_FILE: " + str(path))
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise Refusal("GIT_CONFIG_TOO_LARGE: " + str(path))
        return data


def repository_configs(cwd):
    """Discover metadata without asking Git to follow an unchecked include."""
    cwd = Path(cwd).resolve(strict=True)
    for root in (cwd, *cwd.parents):
        pointer = root / ".git"
        try:
            mode = pointer.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode):
            gitdir = pointer
        elif stat.S_ISREG(mode):
            value = regular_bytes(pointer, 4096).decode().strip()
            if not value.startswith("gitdir: ") or "\n" in value:
                raise Refusal("GIT_POINTER_INVALID")
            raw = Path(value[8:])
            gitdir = Path(os.path.abspath(raw if raw.is_absolute() else root / raw))
        else:
            raise Refusal("GIT_POINTER_UNSAFE")
        if gitdir.resolve(strict=True) != gitdir:
            raise Refusal("GIT_DIRECTORY_ALIAS")
        common = gitdir
        try:
            value = regular_bytes(gitdir / "commondir", 4096).decode().strip()
            if not value or "\n" in value:
                raise Refusal("GIT_COMMON_DIRECTORY_INVALID")
            raw = Path(value)
            common = Path(os.path.abspath(raw if raw.is_absolute() else gitdir / raw))
        except FileNotFoundError:
            pass
        if common.resolve(strict=True) != common:
            raise Refusal("GIT_COMMON_DIRECTORY_ALIAS")
        head = regular_bytes(gitdir / "HEAD", 4096).decode().strip()
        if (not re.fullmatch(r"(?:ref: refs/[^\s]+|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", head)
                or not (common / "objects").is_dir() or not (common / "refs").is_dir()):
            # Git may skip an incomplete .git directory and continue searching
            # ancestors. Refuse instead of validating a different config set.
            raise Refusal("GIT_DIRECTORY_INCOMPLETE")
        return [common / "config", gitdir / "config.worktree"]
    return []


def safe_setting(key, value):
    key, value = key.lower(), value.strip()
    if key in {"core.hookspath"}:
        return value == "/dev/null"
    if key in {"core.fsmonitor", "commit.gpgsign", "tag.gpgsign"}:
        return value.lower() in FALSE
    if key == "credential.helper":
        return value == ""
    if key == "core.pager":
        return value == "cat"
    if key.startswith("pager."):
        return value.lower() in FALSE
    if key.startswith(("alias.", "include.", "includeif.", "filter.", "credential.", "gpg.",
                       "protocol.", "url.", "uploadpack.", "receive.", "mergetool.", "difftool.")):
        return False
    if key in {"core.sshcommand", "core.gitproxy", "core.askpass", "core.editor", "core.worktree",
               "core.attributesfile", "core.alternaterefscommand", "sequence.editor", "interactive.difffilter",
               "diff.external", "gc.recentobjectshook", "fsmonitor.socketdir", "core.fsmonitorhookversion"}:
        return False
    if re.match(r"^(?:diff\..*\.(?:command|textconv)|merge\..*\.driver|remote\..*\.(?:uploadpack|receivepack|vcs|proxy))$", key):
        return False
    # Unknown settings may introduce a new executable hook. Fail closed instead
    # of treating a growing denylist as proof that arbitrary config is data.
    if key in {"core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
               "core.ignorecase", "core.precomposeunicode", "core.symlinks", "core.autocrlf", "core.safecrlf",
               "core.eol", "core.ignorestat", "core.trustctime", "core.checkstat", "core.untrackedcache",
               "core.splitindex", "core.sparsecheckout", "core.sparsecheckoutcone", "core.abbrev",
               "extensions.objectformat", "extensions.refstorage", "extensions.worktreeconfig",
               "user.name", "user.email", "user.useconfigonly", "user.signingkey",
               "pull.rebase", "pull.ff", "push.default", "fetch.prune", "fetch.prunetags", "init.defaultbranch"}:
        return True
    return bool(re.match(r"^(?:branch\..*\.(?:remote|merge|rebase|pushremote|description)|remote\..*\.(?:url|pushurl|fetch|promisor|partialclonefilter))$", key))


def command_context(argv, cwd):
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "-C" and index + 1 < len(argv):
            cwd = Path(cwd) / argv[index + 1] if argv[index + 1] else Path(cwd)
            index += 2
        elif arg == "-c" and index + 1 < len(argv):
            key, sep, value = argv[index + 1].partition("=")
            if not sep or not safe_setting(key, value):
                raise Refusal("GIT_COMMAND_CONFIG_REFUSED")
            index += 2
        elif arg in {"--no-pager", "--literal-pathspecs", "--no-optional-locks"}:
            index += 1
        else:
            break
    if index == len(argv) or argv[index] not in COMMANDS:
        raise Refusal("GIT_COMMAND_UNSUPPORTED")
    command, args = argv[index], argv[index + 1:]
    if command == "config" and not (set(args) <= {"--local", "--null", "--list", "--no-includes"} and "--list" in args):
        raise Refusal("GIT_CONFIG_COMMAND_UNSUPPORTED")
    if command == "remote" and (not args or args[0] != "get-url"):
        raise Refusal("GIT_REMOTE_COMMAND_UNSUPPORTED")
    # Git accepts abbreviated long options: an exact-name denylist would allow
    # --upload-p or --strat to select an unchecked executable.
    if command in {"fetch", "merge"}:
        allowed = {"--quiet", "-q", "--prune", "--no-tags", "--tags", "--"} if command == "fetch" else {"--ff-only", "--quiet", "-q", "--"}
        if any(arg.startswith("-") and arg not in allowed for arg in args) or (command == "merge" and "--ff-only" not in args):
            raise Refusal("GIT_EXECUTABLE_OPTION_REFUSED")
    return Path(cwd).resolve(strict=True)


def environment(runtime, metadata, inherited):
    result = {key: value for key, value in inherited.items()
              if key in {"HOME", "USER", "LOGNAME", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                         "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}}
    result.update(PATH=str(runtime / "bin"), LC_ALL="C", LANG="C", GIT_CONFIG_NOSYSTEM="1",
                  GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
                  GIT_EXEC_PATH=metadata["git_exec_path"], GIT_ALLOW_PROTOCOL="https:http",
                  GIT_PAGER="cat", GIT_ATTR_NOSYSTEM="1")
    return result


def validate_configs(binary, paths, env):
    for path in paths:
        try:
            data = regular_bytes(path)
        except FileNotFoundError:
            continue
        # Git parses its own syntax, but only the bounded bytes already read.
        # Never let its parser open a repository-controlled include or alias.
        cp = subprocess.run([binary, "config", "--file", "/dev/stdin", "--no-includes", "--null", "--list"],
                            cwd="/", env=env, input=data, capture_output=True, timeout=15)
        if cp.returncode:
            raise Refusal("GIT_CONFIG_INVALID: " + str(path))
        for item in cp.stdout.decode().split("\0"):
            if not item:
                continue
            key, _, value = item.partition("\n")
            if not safe_setting(key, value):
                # Config values and URL-bearing subsection names may be secrets.
                raise Refusal("GIT_CONFIG_EXECUTION_REFUSED: " + str(path))


def main():
    runtime = Path(__file__).resolve().parent
    metadata = json.loads((runtime / "runtime.json").read_text())
    argv = sys.argv[1:]
    cwd = command_context(argv, Path.cwd())
    env = environment(runtime, metadata, os.environ)
    binary = metadata["tools"]["git"]
    validate_configs(binary, repository_configs(cwd), env)
    defaults = [value for setting in OVERRIDES for value in ("-c", setting)]
    # Overrides precede argv; disallowed caller settings have already refused.
    os.execve(binary, [binary, "--no-pager", *defaults, *argv], env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("forge candidate Git: " + str(exc), file=sys.stderr)
        sys.exit(2)
