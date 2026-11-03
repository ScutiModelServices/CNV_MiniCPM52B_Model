# Changelog

## Unreleased

- Load saved CPU thread settings in `main()`, matching LayaModel and preserving injected clients in tests.
- Shared admin health rendering uses backend readiness and model fields while retaining each service's display.
- Deployment reference covers the shared Ollama stack, settings, scaling, updates and releasing.
- `MINICPM_WARM` (default `1`) loads the model with an empty chat at startup; connection failures are logged.
- Updates card and admin routes check remote manifest digests without downloading, then pull changed weights
  and unload the old Ollama copy on demand. All replicas use the new weights on their next request.
- `MINICPM_UPDATE_CHECK_HOURS` (default `6`, `0` disables the timer) controls background checks; manual checks remain available.

## 1.0.0

First public release.

- Admin page at `/admin/`: health badge with polling, token list with created, last used and request count,
  copy button, in-app revoke dialog, CPU thread control, light and dark themes, phone layout.
- Per-project tokens stored as hashes; `last_used` and `requests` are written at most once a minute per token,
  so the counters are approximate and a crash can lose up to a minute of them.
- Docker Compose deployment behind nginx with configurable replicas; guides for Docker on the same device,
  Docker on a deployment device, and no Docker.
- Browser smoke test (Playwright) and a GitHub Actions workflow that runs the tests.
