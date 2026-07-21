# Contributing to seller-agent

Thanks for contributing! Beyond the usual test/lint expectations enforced by
CI, this repository enforces a hygiene policy designed to keep internal
tooling references and credentials out of a public codebase.

## One-time setup after cloning

```bash
./scripts/setup-hooks.sh
```

This points `core.hooksPath` at the committed `.githooks/` directory, enabling
the guardrails below locally. CI enforces the same rules
(`.github/workflows/hygiene.yml`), so running the setup script just saves you a
failed CI round-trip.

Optionally install [gitleaks](https://github.com/gitleaks/gitleaks)
(`brew install gitleaks`) so the pre-commit hook can scan staged changes for
secrets locally; if it is not installed the hook warns and CI still scans.

## Repository hygiene policy

This is a public repository. The following must never appear here:

1. **Internal tracker IDs in commit messages.** No `ar-*` bead IDs (internal
   Agent Range issue tracker) — ever. `bead:` trailers are allowed only for
   this repo's own public prefix: `seller-*`.
2. **Session URLs.** No `Claude-Session:` trailers or other internal AI-session
   URLs in commit messages.
3. **Issue-tracker data files.** Nothing under `.beads/` may be committed.
4. **Real secrets as env defaults.** Never ship a real credential as a
   `${VAR:-default}` fallback in compose files, env files, or scripts. Use
   fail-fast `${VAR:?}` syntax, or an obvious placeholder such as
   `change-me-in-production`. The pre-commit hook blocks defaults that look
   secret-like (long, mixed-case, digits/symbols) when the variable name
   suggests a credential (`secret`, `token`, `key`, `passw*`).
5. **Secrets in general.** gitleaks runs on staged changes locally (when
   installed) and on every push/PR range in CI. For a confirmed false
   positive, add a `# gitleaks:allow` comment on the flagged line.

### How CI applies the commit-message policy

The `hygiene` workflow checks only the incoming commit range (PR base..head,
or the pushed range) — historical commits on `main` are not re-scanned.
Within the range, merge commits and commits created before the policy was
introduced (2026-07-21) generate warnings rather than failures, so a
long-lived branch or a merge from old history cannot make your PR fail
retroactively. All other commits in the range fail the build on violations;
fix them with `git commit --amend` or `git rebase -i` and force-push your
branch.
