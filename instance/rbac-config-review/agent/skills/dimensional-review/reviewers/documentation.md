# Documentation review

Check changed documentation against the implementation and tests, and check
whether behavior changed by the PR requires new or updated user, API,
configuration, deployment, or operator documentation. Inspect the relevant
existing documentation and examples even when no documentation file changed.
Consider whether a reader can discover and safely use the changed behavior.

Report a missing, stale, or incorrect document only when the changed behavior
creates a concrete need and the consequence for a reader is clear. Cite the
changed code or documentation and the relevant existing documentation or
contract; when documentation must be created, explain where it belongs and
what it needs to cover. Anchor findings to a changed PR line and use only
sources pinned in the review context. Do not require documentation for
internal changes without a reader-facing effect.
