"""No-op stub for the clone prerequisite named by the diagnosis and docs SOPs.

Directives: ``directives/diagnose_repo.md`` and ``directives/generate_docs.md``.
Real clone and fork-ownership verification are intentionally not implemented.
"""


def main() -> int:
    """Return successfully without cloning, fetching, or changing a repository."""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
