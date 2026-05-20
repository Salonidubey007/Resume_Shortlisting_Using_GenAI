# CHANGELOG

All changes to this project are recorded here in reverse-chronological order.
Every entry includes the date, the files modified, the reason, and a detailed
description of what was changed so the next team member can follow the history
without reading the full diff.

---

## [2025-07-14] — Resume Versioning & Archival System

### Requirement (from user)
> "When a resume is updated by a colleague, the old discarded resume should be
> appended to the array in a file specific to that profile.  Each archived file
> should be named as the Emp_ID_Profile_Name.  The 6th resume should be the
> current one used against the JD for shortlisting and all previous 5 versions
> should be appended to the array in the file specific to that profile."

Three root-cause problems were identified and fixed:

| # | Problem | Root Cause | Fix Applied |
|---|---------|-----------|-------------|
| 1 | Same filename re-upload → old cached JSON served instead of reparsing | `_parse_single_resume` only checked `out_json_path.exists()` — no content comparison | SHA-256 hash of the file is now stored in `.file_hashes.json`; cache is only used when the hash matches |
| 2 | Different filename for same employee → treated as a new profile, causing duplication | No Emp_ID-based identity check before parsing | `_build_empid_index()` scans existing JSONs by `Candidate_Emp_ID`; if the incoming file's Emp_ID prefix matches an existing record the old JSON is archived and the stale file is removed before parsing the new one |
| 3 | Archive was one timestamped file per event, not a single array per employee | `_parse_single_resume` wrote `{stem}_{timestamp}.json` per archive event | New `_archive_old_version()` function maintains a single `{Emp_ID}_{Profile_Name}.json` file per employee in `storage/resume_jsons/archived/`; each superseded version is **appended** to the JSON array inside that file with an `_archived_at` timestamp |

---

### Files Modified

#### `engines/resume_engine.py`  ← **only file changed**

##### New constants / paths added
```
HASH_STORE_PATH = RESUME_JSONS_DIR / ".file_hashes.json"
```
A hidden JSON file that persists `{ "filename.docx": "sha256hex", ... }` across
runs so content-change detection survives server restarts.

##### New functions added

| Function | Purpose |
|----------|---------|
| `_load_hash_store() -> Dict[str, str]` | Loads the persisted filename→SHA-256 map from `.file_hashes.json` |
| `_save_hash_store(store)` | Writes the updated hash map back to disk |
| `_build_empid_index() -> Dict[str, Path]` | Scans `storage/resume_jsons/*.json` and returns `{emp_id_lower: json_path}` for identity resolution |
| `_extract_empid_from_filename(stem) -> str` | Extracts the leading numeric Emp_ID token from a filename stem (e.g. `30005909_Name` → `30005909`) |
| `_archive_old_version(old_json)` | Appends the old JSON object (with `_archived_at` timestamp) to the employee's archive array file named `{Emp_ID}_{Profile_Name}.json` inside `storage/resume_jsons/archived/` |

##### `_parse_single_resume` — logic rewritten (Step-by-step)

Old flow:
```
exists? → return cache
else    → parse → if exists archive as {stem}_{ts}.json → write new
```

New flow:
```
Step 1  Compute SHA-256 of incoming file
Step 2  Emp_ID index lookup
          → if different filename maps to same Emp_ID: flag existing_json_path
Step 3  Cache-hit check
          → only skip LLM if: same filename AND hash matches AND not force_reparse
Step 4  Archive old version (if any) via _archive_old_version()
          → if old JSON was under a different stem: delete stale file + clean hash store
Step 5  Read & parse resume (LLM, compact/chunked)
Step 6  Validate schema
Step 7  Write new individual JSON
Step 8  Persist updated hash to .file_hashes.json
```

##### `detect_updated_resumes` — improved

- Now uses `_build_empid_index()` and `_extract_empid_from_filename()` for
  consistent identity resolution (same logic as `_parse_single_resume`).
- Hash comparison added: if a file's Emp_ID is known but the content hash is
  unchanged, it is classified as `"new"` (not truly updated) to avoid
  unnecessary re-parses.

##### `_rebuild_master_from_individual` — deduplication improved

- Now deduplicates by `Candidate_Emp_ID` first (in addition to `Source_file`)
  so that if two JSON files for the same employee somehow coexist, only one
  entry appears in the master.
- Skips files whose names start with `.` (e.g. `.file_hashes.json`).

##### `parse_resumes` — simplified

- Removed the split `force_reparse` / normal-append branching.
- Always calls `_rebuild_master_from_individual()` after parsing so the master
  is always consistent regardless of mode.
- Public function signature is **unchanged** — no callers need updating.

---

### Archive File Format

`storage/resume_jsons/archived/{Emp_ID}_{Profile_Name}.json`

```json
[
  {
    "Candidate_Emp_ID": "30005909",
    "Candidate_name": "Battini Ajay Kumar",
    "Source_file": "All_RESUMES_30005909_Battini_Ajay_Kumar_Resume.docx",
    "_archived_at": "2025-07-14T10:23:45",
    ...
  },
  {
    "Candidate_Emp_ID": "30005909",
    "Candidate_name": "Battini Ajay Kumar",
    "Source_file": "All_RESUMES_30005909_Battini_Ajay_Kumar_Resume_v2.docx",
    "_archived_at": "2025-07-14T14:05:12",
    ...
  }
]
```

The **current** (latest) version always lives in
`storage/resume_jsons/{stem}.json` and is used for scoring.
All previous versions are in the archive array above.

---

### No Other Files Were Modified

| File | Status |
|------|--------|
| `app.py` | Unchanged |
| `engines/jd_engine.py` | Unchanged |
| `engines/scoring_engine.py` | Unchanged |
| `utils/file_reader.py` | Unchanged |
| `utils/json_tools.py` | Unchanged |
| `utils/llm_client.py` | Unchanged |
| `utils/logger.py` | Unchanged |
| `utils/parallel.py` | Unchanged |
| `utils/schema_validator.py` | Unchanged |
| `utils/text_cleaner.py` | Unchanged |
| `prompts/RS_Prompt.md` | Unchanged |
| `templates/index.html` | Unchanged |

---

*Changelog maintained by: Amazon Q Developer*
*Next team member: append new entries at the TOP of this file, above this entry.*
