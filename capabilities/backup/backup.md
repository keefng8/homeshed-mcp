# Backup — export, restore

## ID
`backup.export`, `backup.restore` (risk `write`; owner only, HomeShed Pro)

## Purpose
HomeShed's own settings and state in one file, to keep safe or move to a new machine (the owner, 2026-10-23).
R&D's verdict (`researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md`): a plain zip with a sha256
manifest, restore as a dry run then a confirm, unsafe names refused, no secret values inside.

Pro only (the owner, 2026-10-24). The zip is written to this machine's own data folder; nothing is uploaded.

## What's in a backup
The state files under the data folder (`DATA_DIR`): settings, schedules, watched pages, app access (grants and
token *hashes*, never tokens), the to-do list, approvals, observations, counters. Left out: the vault folder (only
the **names** of stored keys go in, listed as `vault_key_names`), generated images, other services' folders, lock and
temp files, and the activity logs unless `include_logs` is true. Any file with a secret-shaped value in it (the
same patterns as `secrets.transcript_scan`) is left out and named in `skipped_secret`. The zip isn't encrypted
(`encrypted: false`): it holds no secret values, but it does show how your HomeShed is set up, so keep it private.

## export
`backup.export(include_logs=false)` writes `usage/backups/homeshed-backup-YYYYMMDD-HHMMSS.zip` in the data folder (env
`BACKUPS_DIR`) and keeps the newest 10. Returns `{file, bytes, files, skipped_secret, vault_keys, encrypted}`.

## restore
1. `backup.restore(file)`: a dry run. Every file's checksum and name is checked (no `..`, no absolute paths, no
   hidden or skipped folders), and it lists what's `new`, `changed` and the same, the vault keys to re-enter, and a
   `confirm` code (valid 10 minutes, this backup only).
2. `backup.restore(file, confirm=code)`: makes a safety backup of the current state first, then writes each file
   (via a temp file, so a failure never leaves half a file). Restart the tool server afterwards, then re-enter the
   vault keys under Control Panel > Settings > Credentials.

A backup from another machine: copy it into that machine's backups folder first.
