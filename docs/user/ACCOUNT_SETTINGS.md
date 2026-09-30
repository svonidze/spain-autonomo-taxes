# Account settings

Open **Settings / Настройки** in the navigation or the SQLite footer. Settings are
available even when the database has no tax periods.

## Taxpayer profile

The form edits the full name, tax identifier and country of residence. Values
are normalized consistently with the existing profile CLI. If several profiles
already exist, select the profile to edit; this does not switch the accounting
application to a different taxpayer. The top bar continues to show the first
created profile. The UI creates a profile only when no profile exists.

Each creation or change is recorded transactionally in `taxpayer_profile_changes`
with previous/current values, row versions, time and the authenticated actor
when available. Concurrent edits are rejected; the form retains the draft so
it can be compared with current values before retrying.

Tax identifiers cannot be changed through this form after any closed/amended
period or filing snapshot exists. An operator must handle such corrections as
an explicit historical-data procedure. Existing exports, archived documents,
filed returns and accounting entries are never rewritten by this screen.
Nothing is submitted to AEAT.

Registered activities, AEAT/IAE codes and IRPF/IVA regimes are shown read-only.
Changing these facts still requires the source-backed activity workflow.
Language selection uses the existing browser preference; taxpayer data and
settings drafts are not persisted in browser storage.

## Backup preferences and observed status

The UI stores requested **local** retention limits in
`AUTONOMO_PRIVATE_ROOT/account-backup-settings.json` (private mode 0600, atomic
replacement). This file is inside the existing private-root archive boundary.
An explicit, absolute `AUTONOMO_PRIVATE_ROOT` must be present in the web service
environment. The location of `--config` is not used: in SOPS mode it points to
a different, immutable secret generation. If no explicit root is available,
the backup form is unavailable and profile settings remain independent.

- Daily limit: 7–365 local copies; monthly limit: 3–120.
- A blank field means operator managed. The backup job uses the existing
  `AUTONOMO_BACKUP_KEEP` / `AUTONOMO_MONTHLY_BACKUP_KEEP`, then its existing defaults
  of 35 / 13. The web does not predict the values in the job's environment.
- Every policy save requires explicit confirmation. On a subsequent backup,
  older local archives **and manifests** beyond the selected limit are
  permanently deleted. The web cannot know the exact number of deletions.
  For operator-managed values, even the numeric limit is unknown to the web.
- Saving does not run a backup, change timers, configure cloud storage or
  verify recovery. Remote retention remains independent of local retention.

The page reports observed runs from the per-class markers: time, local readiness,
whether offsite upload was acknowledged, and the retention limit actually used.
It separately shows the latest monthly recovery-verification attempt and the
last successful isolated restore. Older markers do not
prove support for the new preferences. Missing, malformed or future-dated
markers are unknown, not a green health signal. A past successful upload is not
proof that the cloud is healthy now or that a restore has been tested.

## Release and control-plane compatibility

This change requires **database schema 23**. Apply it through the existing
reviewed deployment/migration procedure, after verified backups. Web requests
never auto-migrate an operational database. Schema errors instruct an operator
to upgrade; rollback must follow the existing database recovery procedure.

Both the default and SOPS control-plane installers must be updated along with
the application. They install the same standalone `backup_settings.py` reader
beside `backup.sh`; both preflight variants validate any existing preferences.
The reader is independent of the current release's Python environment so
rolling back the application does not remove backup preference support.
Updating only the web release leaves an old installed backup job unable to
consume preferences. The UI therefore labels saved values as requests and
reports application only through subsequent success markers.

Use the established operations workflow to update the control plane. Do not
run an installer just to inspect settings: installers can activate timers.
No production deployment, operational migration or control-plane installation
is performed merely by merging this UI change.

Malformed preferences fail closed before archive creation or pruning and are
also rejected by preflight. The UI does not overwrite a corrupt file; an
operator must repair it with a known valid policy. The existing failure alert
and backup-freshness checks remain in force. Credentials, arbitrary paths,
remote destinations, restore controls and timer activation are deliberately
outside this web form.
