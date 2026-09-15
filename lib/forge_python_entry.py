"""Candidate isolated Python entry; supports the forms used by copied Forge helpers."""

import sys
from pathlib import Path


def main():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise SystemExit("Candidate Python requires -I -S -B")
    runtime = Path(__file__).resolve().parent
    sys.path.insert(0, str(runtime / "vendor"))
    args = sys.argv[1:]
    if not args:
        raise SystemExit("Candidate Python refuses interactive input")
    if args[0] == "-c" and len(args) > 1:
        filename, text, sys.argv = "<string>", args[1], ["-c", *args[2:]]
    elif args[0] == "-":
        filename, text, sys.argv = "<stdin>", sys.stdin.read(), args
    elif not args[0].startswith("-"):
        filename = str(Path(args[0]).resolve(strict=True))
        # A host wrapper must never execute a script supplied by a worker.
        if not Path(filename).is_relative_to(runtime.parent):
            raise SystemExit("Candidate Python refuses scripts outside its protected package")
        text, sys.argv = Path(filename).read_bytes(), args
    else:
        raise SystemExit("unsupported Candidate Python invocation")
    scope = {"__name__": "__main__", "__file__": filename,
             "__package__": None, "__spec__": None, "__builtins__": __builtins__}
    exec(compile(text, filename, "exec"), scope)


if __name__ == "__main__":
    main()
