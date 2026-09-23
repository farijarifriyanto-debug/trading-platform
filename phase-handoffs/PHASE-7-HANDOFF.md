# Phase 7 Handoff — Production Hardening

Date: 2026-09-23
Branch: `phase7-production-hardening`
Target: paper/research production-ready candidate
Live trading: **OFF**
Production cutover: **NOT PERFORMED**

## Implemented

- SQLite WAL/FULL-sync durable paper cash and position state.
- Paper broker persistence rollback if durable write fails.
- SQLite durable research queue with idempotency keys, leases, heartbeat, bounded retries, cancellation, stale-worker recovery, and terminal results.
- Supervised queue worker for experiment, AI-candidate, and FinRL-X research jobs only.
- Mutation Bearer-auth gate when `TRADING_REQUIRE_AUTH=1`.
- Request-size ceiling, mutation rate limiting, and security headers.
- `/ready`, `/security/status`, and Prometheus-compatible `/metrics/prometheus`.
- Dashboard visibility for durable jobs and readiness.
- Consistent backup/verify/restore tooling with SQLite backup API and SHA-256 manifest.
- Hardened systemd candidate API/worker units; worker network isolation.
- Hardened API-only Docker candidate: loopback binding, non-root image user, read-only rootfs, tmpfs, cap-drop ALL, no-new-privileges, readiness healthcheck.
- Production preflight script and credential-pattern scan.

## Acceptance evidence

- Unit/API: **58 passed**.
- Production preflight: **PASS**.
- `git diff --check`: **PASS**.
- Docker Compose config: **PASS**.
- systemd unit syntax verification: **PASS (rc=0)**.
- Durable queue -> worker -> native experiment: **PASS**.
- Backup -> verify -> restore drill: **PASS**; restored SQLite integrity, dataset, and completed job.
- Auth smoke: unauthorized mutation **401**; authorized malformed request reached validation **422**.
- Concurrent HTTP smoke: **200/200 requests PASS** against readiness/metrics with 20 workers.
- Hardened Docker candidate: `/health`, `/ready`, security status **PASS**; image user = `app` (non-root).
- GitHub PR CI: **PENDING** until branch push.

## Locked boundaries

- No live exchange-order code path.
- Paper execution remains the only execution-capable path.
- Durable background queue does not accept paper/live order jobs.
- AI/FinRL remain research-only and cannot authorize execution.
- Nautilus/LEAN remain simulation/backtest paths.
- No production service was restarted, replaced, or cut over during Phase 7 acceptance.
- Real secrets are not committed; production auth key belongs in `/etc/trading-platform.env` mode 0600.

## Recovery

- Runtime state: `runtime.sqlite3`.
- Job state: `jobs.sqlite3`.
- Backups use `scripts/backup-data.py`.
- Stale RUNNING jobs return to QUEUED after lease expiry.
- Long jobs refresh leases through worker heartbeat.
- Paper in-memory mutation is rolled back if persistence fails.

## Next

Phase 7 is ready to freeze after GitHub CI and main CI pass. Phase 8 live trading, if pursued, is a separate execution/security/risk project and must not reuse this phase as implicit authorization to place real orders.
