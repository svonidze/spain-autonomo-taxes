---
name: deploy-prod
description: Deploy a reviewed Spain autonomo taxes release to the existing production service, with exact-SHA checks, verified backups, migration and rollback. Use for production deployment or rollback requests in this project, including «деплой на прод». For diagnosis or planning requests, perform read-only checks only; do not deploy.
---

# Deploy the existing production service

Use the installed operations control plane, not an improvised copy/upload-and-restart flow. This skill supplies routing and acceptance gates; the repository runbooks own command details.

## Resolve the target and authority

- Read [operations](../../../ops/README.md), especially the operating checklist, deployment, migration and rollback sections. Read [disaster recovery](../../../docs/DISASTER_RECOVERY.md) before backup verification or recovery. Inspect these files at the selected application SHA, not an outdated working tree.
- Load the operator's private target inventory, normally `~/.config/autonomo-tax/deploy-target.json`, or an explicitly supplied path. Expected fields: `ssh_host`, `service_user`, `ops_root`, `runtime_env_path`, `release_root`, `repository_path`, `private_root`, `healthcheck_url`, `mode`. Treat values as data, never shell code. Keep the inventory outside Git with private permissions; it contains addresses and paths, not credentials.
- Verify the inventory against the active service and its runtime file. Missing inventory, an unexpected host, changed baseline or conflicting deployment mode requires clarification. Never infer the service user from the operator's default SSH login.
- A deployment request authorizes the agreed release workflow, not account changes, a SOPS transition, new backup destinations, accounting repairs or unrelated ops upgrades. Read-only review/planning requests do not authorize production writes.
- Record the active application SHA, live schema and intended full lowercase 40-character target SHA. If no SHA was supplied, resolve the remote default branch once and pin its current reviewed, CI-green commit before mutations. Do not silently change an already approved target when the branch advances. Unmerged releases require explicit approval of that PR head.
- `mode=default` uses the installed one-SHA deploy script. If SOPS is actually active, read the [SOPS runbook](../../../ops/sops/README.md), resolve both reviewed SHAs and their binding, and follow that mode exclusively. An existing SOPS directory does not prove activation. Do not mix scripts or switch modes to bypass a failed check.

## Preflight before downtime

- Connect as the service user with SSH host-key verification. Do not print runtime/configuration secrets, dump credentials or enable shell tracing. Load runtime with the runbook's validated loader, never `source runtime.env`.
- For an unattended operational wrapper, use the same `EnvironmentFile` as the service (for example on a transient user-systemd unit). The runtime loader preserves nonempty inherited variables, including PATH; merely calling it does not reproduce the service environment. Check required tools such as rclone in the effective wrapper environment before stopping the web service.
- Check CI for the exact target, its reachability from the selected remote ref, the active unit's real executable/runtime, database integrity and foreign keys, storage readiness, backup results and timer state. Check available space for the release, local and downloaded archives, and unpacked data; compressed archive size alone is insufficient.
- Verify that the previous release, venv, release/schema markers and unit exist. Record this recovery target privately. A successful HTTP bootstrap is not schema validation.
- Determine whether the target changes the schema. For migration, require a write-free maintenance window through final acceptance and a verified pre-migration snapshot; never start old code against a newer schema.
- Check OCR using the helper with the exact service runtime file: `python3 <helper> --runtime-env <runtime-file>`. See [OCR provisioning](../../../ops/PROVISIONING.md#local-ocr-dependency). A check against the operator's inherited environment is insufficient. Missing dependencies or runtime PATH is a blocker, not permission to change configuration or disable the gate.
- If the exact target is already running and all acceptance checks pass, report that state without redeploying. If a release directory exists from an interrupted attempt, inspect its installation and markers before retrying: directory existence does not prove the venv is complete. If incomplete, stop the rollout, preserve the partial release for diagnosis and restore a compatible baseline service if it was stopped. Do not retry deploy against that directory, delete it or repair the immutable release in place without a separately scoped recovery decision.

## Installed ops updates, only when needed and in scope

Read the operations section **Updating the OCR deployment checks in an existing ops installation**. Compare installed files with the reviewed source; publishing application code does not update installed helpers.

For an authorized, scoped update, take the shared operations lock in a short separate process and retain private previous copies, modes and digests. Install the OCR helper first (`0644`), then its calling deployment script (`0755`), using temporary siblings and atomic replacement. Validate the installed helper and preflight. Restore callers before restoring the helper on failure; an added unused helper may remain.

Exit the lock-owning process before invoking backup, deploy or rollback: those scripts independently acquire the same nonblocking lock. Do not wrap them in a second outer lock. Do not run the general installer as a shortcut: it also changes units and timers. In a default-only rollout, leave SOPS callers untouched unless their update was separately included; record that boundary rather than claiming the SOPS path was upgraded.

## Backup, deploy and acceptance

1. Announce the maintenance window and exclude user/CLI writes and concurrent operational jobs. Preserve timer configuration. If the web service is stopped manually, keep an explicit early-failure recovery path active from that point; failures before the deployment script's service-switch stage do not restart it automatically.
2. Run the installed backup with its configured destinations. This is a production change and may prune retained archives, upload evidence and reconcile configured replicas. Do not add broad reconciliation as a smoke test.
3. Download the exact new archive and manifest through the configured crypt remote into fresh private scratch storage. Follow the recovery runbook using `scripts/restore_private_root.py` and a new empty extraction directory. Validate archive/member hashes and read-only SQLite integrity, foreign keys and expected schema. Never use live restore or launch the restored production configuration for this drill.
4. Record the accounting baseline after the backup job finishes: configured reconciliation can occur after its full-root archive was made. For a migration, the deployment script's later pre-migration SQLite snapshot is the precise rollback point; record its exact returned path and verify ownership, mode `0600`, integrity and old schema.
5. Invoke the installed deployment command with the pinned SHA and an explicit transport ref. For a required storage/schema migration, use the runbook's one-shot `AUTONOMO_ENABLE_STORAGE_MIGRATION=1`; do not persist the flag in runtime configuration. Record returned current/previous SHAs. On interruption, inspect actual state before retrying.
6. Before releasing the write-free window, verify the selected SHA in the active executable and `current`, service health and live schema. For migration, inspect the rendered unit for the required migration `ExecStartPre` and readiness environment flag, and verify startup success. Check integrity/foreign keys and compare accounting data with the recovery baseline, allowing only reviewed migration effects.
7. Check HTTPS root/bootstrap, representative read-only application endpoints and new static resources without exposing payloads. Verify selected existing evidence reads from the configured providers without uploading. When OCR behavior changed, perform extraction-only inspection of a selected image; never ingest or post a test document. Record external-provider verification gaps separately from application health.
8. Confirm timers retain their prior configuration and the previous release/snapshot remain available. Report acceptance evidence and any gap; a pre-existing provider outage is not by itself justification to restore the whole accounting database. Do not claim full readiness when a required check is incomplete.

## Failure and rollback boundaries

- Before the release switch: if manual shutdown occurred and `current`, database schema/integrity and the saved unit still match the baseline, restart that baseline unit and recheck its health. Stop the rollout and report the cause.
- After a deployment failure: first verify what built-in compensation actually restored. It is not a guarantee. Do not restart an incompatible old unit or blindly repeat the deploy.
- A post-switch schema mismatch is a failed rollout even if HTTP returns 200. Keep the write-free window, stop the incompatible new unit, and inspect the actual code/schema and recovery artifacts. If the live database still matches the saved baseline and no new writes occurred, use the installed mode-appropriate rollback to that compatible baseline; otherwise follow the verified-snapshot or uncertain-state rules below. Never run an ad hoc migration to make a failed acceptance check pass.
- After a completed switch, if rollback is needed while new writes are excluded, use the installed mode-appropriate rollback. A cross-schema rollback needs the exact verified old-schema snapshot via the runbook's `AUTONOMO_ROLLBACK_SNAPSHOT`; verify the resulting code, schema and data reads, not only bootstrap.
- If `current` already points to the intended rollback SHA but the database is incompatible, the ordinary rollback command cannot repair this state. If state is uncertain, a snapshot is missing/invalid, integrity fails or later writes exist, preserve evidence, stop incompatible writers and request recovery direction. Never silently discard later records or perform an improvised database replacement.
- Keep snapshots, operational logs, provider locators and inventory outside Git. End with the deployed SHA/schema, checks, private recovery-artifact locations and remaining limitations. A commit, PR or green CI is not proof of deployment.
