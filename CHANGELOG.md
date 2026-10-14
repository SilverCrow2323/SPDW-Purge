# Changelog

## 1.1.0 — first public release
- English UI, restructured as a Python package (`spdw_purge`) with tests, CI and `.deb` packaging
- **SCAN**: interactive squarified **treemap** (click to select, double-click to dive, UP to go back),
  **file-type breakdown**, right-click menu (open in file manager, copy path, scan folder),
  Markdown **report export**, live size bookkeeping after deletions
- **FINDER** tab: duplicate files (size → head hash → full hash, hardlink-aware), empty files,
  empty folders, broken symlinks, big & old files, "keep oldest, select the rest"
- **JUNK**: new categories — MX Linux snapshots, Docker/Podman prune, huge active logs (truncate),
  privacy traces; scrollable list
- **DRY-RUN** switch (GUI and `--dry-run`) that rehearses every operation
- **Audit log** of every action in `~/.local/state/spdw-purge/actions.log`
- **CLI**: `--list`, `--clean safe|all|keys`, `--top PATH`, `--yes`, `--selftest`
- APPS: "> 10 MB only" filter, dependency-dragging simulation, critical-package detection

## 1.0.0 — private prototype
- Single-file Tkinter app: scan tree, junk cleaner, package purge with leftover hunting
