# CLI entrypoint for the knowledge pipeline.
#
# For full Dagster UI:
#   dagster dev
#   uv run poe dev
#
# For one-shot job execution:
#   uv run poe index
#   uv run poe backup
#
# This module provides a thin wrapper for backward compatibility:
#   python -m knowledge_pipeline dev         → launch Dagster UI
#   python -m knowledge_pipeline index       → run index job once
#   python -m knowledge_pipeline backup      → run backup job once

import subprocess
import sys


def main() -> None:
    args = sys.argv[1:]
    module = "knowledge_pipeline.definitions"

    if not args or args[0] == "dev":
        subprocess.run(["dagster", "dev", "-m", module], check=True)

    elif args[0] == "index":
        subprocess.run(
            ["dagster", "job", "execute", "-m", module, "-j", "index_knowledge_job"],
            check=True,
        )

    elif args[0] == "backup":
        subprocess.run(
            ["dagster", "job", "execute", "-m", module, "-j", "backup_job"],
            check=True,
        )

    else:
        print(f"Unknown command: {args[0]}")
        print("Usage: python -m knowledge_pipeline [dev|index|backup]")
        sys.exit(1)


if __name__ == "__main__":
    main()
