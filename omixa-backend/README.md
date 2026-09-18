# Omixa Backend (V1 scaffold)

No database. No persistent storage. Every request is one job, backed
by a temp folder that gets deleted after download (or after 30 min
if abandoned).

## Structure

```
omixa-backend/
├── app.py              entry point, registers routes
├── config.py           temp dir, upload limits, allowed file types
├── routes/
│   ├── upload.py        POST /api/upload         -> job_id
│   ├── process.py       POST /api/process/<id>    -> runs cleaning
│   └── download.py      GET  /api/download/<id>   -> cleaned file
├── processing/
│   └── pipeline.py      read -> clean -> export orchestration
├── cleaning/
│   ├── rules.py          auto-fix cleaning rules (see below)
│   ├── quality_report.py read-only diagnostics report (detect-only findings)
│   └── detectors.py      shared heuristics (email/phone/date/etc. detection)
├── export/
│   └── exporter.py      writes cleaned df back to csv/xlsx
└── utils/
    └── file_handler.py  job folders, save/find/delete temp files
```

## Flow

```
POST /api/upload            -> { job_id }
POST /api/process/<job_id>  -> { status, summary }
GET  /api/download/<job_id> -> cleaned file, then temp data is deleted
```

## Run locally

```
pip install -r requirements.txt
python app.py
```

Server starts on `http://localhost:5000`. `GET /api/health` for a
quick check it's alive.

## Deploying (Railway, Render, etc.)

A `Procfile` is included:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Point the host at this repo, let it run `pip install -r
requirements.txt`, and it'll pick up the Procfile automatically on
Render/Railway/Heroku-style platforms. `app.py` exposes a module-level
`app` object for gunicorn to import directly (`app:app`), separate
from the `python app.py` dev entry point.

Before sharing a deployed link with your team: set `OMIXA_API_KEY`
(see "Security" below), without it, there's no auth on any
endpoint. `Config.MAX_CONTENT_LENGTH` (in `config.py`) caps uploads
at 25MB, bump that if you expect larger files.

### Deploying to Railway

This repo also ships a `railway.json` (pins the start command
explicitly) and a `.python-version` (pins Python 3.12, matching
what `pandas==2.2.2` has wheels for, without it Railway may build
against a newer Python and fail to install pandas).

1. Push this `omixa-backend/` folder to a GitHub repo (or a
   subfolder of one, if it's a subfolder, set the Railway service's
   **Root Directory** to `omixa-backend`).
2. In Railway: **New Project → Deploy from GitHub repo** → pick the
   repo.
3. Railway auto-detects Python via Nixpacks and uses the
   `railway.json` start command / `Procfile`. No extra environment
   variables are required for this scaffold.
4. Once deployed, Railway assigns a URL under **Settings →
   Networking → Generate Domain**. That's your new app URL.
5. If you're running the frontend from Netlify separately, update
   `static/config.js` to point `OMIXA_API_BASE` at that Railway URL.
   If Flask is serving `static/` directly (the default), there's
   nothing else to change.

Railway's free tier doesn't cold-start/sleep an app the way Render's
free tier does (the "waking up" animation you see on Render after a
period of no traffic), a Railway deploy stays up, though free usage
is capped by monthly credit rather than uptime, so very low traffic
is cheap but the service can still be paused if that credit runs
out.

## Deploying the frontend to Netlify

Netlify only runs static sites and short-lived serverless functions, it can't run this Flask/pandas backend, which keeps job state on disk
across the upload → process → download requests. So the split is:

- **Backend** → Render/Railway (the Procfile above)
- **Frontend** (`static/`) → Netlify

Steps:

1. Deploy the backend to Render/Railway first and note its URL
   (e.g. `https://omixa-backend.onrender.com`).
2. Edit `static/config.js` and set:
   ```js
   window.OMIXA_API_BASE = "https://omixa-backend.onrender.com";
   ```
3. Push to Netlify. `netlify.toml` is already set to publish the
   `static/` folder with no build step, so a git-linked deploy or a
   drag-and-drop of the `static/` folder both work.

CORS is already open on the backend (`flask_cors`), so the
Netlify-hosted frontend can call the Render/Railway backend across
origins without extra setup.

## Security

V1 ships with three lightweight protections, all in `utils/security.py`
(wired into `app.py`'s `before_request`) and `export/exporter.py`:

- **API key auth.** Set `OMIXA_API_KEY` (any string, generate one
  with `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
  and every `/api/*` request except `/api/health` must send a
  matching `X-API-Key` header, or it gets a 401. **Unset by default**
  so `python app.py` still works with zero setup locally, this means
  the API has no auth at all until you set this. Do it before
  deploying anywhere public. If the frontend is calling a separately
  hosted backend, also set `window.OMIXA_API_KEY` in
  `static/config.js` to the same value.
- **Rate limiting.** `RATE_LIMIT_PER_MINUTE` (default `30`) caps
  requests per client IP per rolling minute on `/api/*` routes, with
  a `429` + `Retry-After` response once exceeded. This is a simple
  in-memory counter, cheap, zero extra dependencies, but tracked
  per worker process (so `gunicorn --workers 2` gives roughly double
  the effective limit) and doesn't coordinate across multiple
  instances. Treat it as a baseline, not a substitute for
  platform-level rate limiting if you scale beyond one instance. Set
  to `0` to disable.
- **CSV/Excel formula-injection defense.** Every exported file
  (cleaned CSV/XLSX) has any cell starting with `=`, `+`, `-`, `@`,
  tab, or carriage return prefixed with a single quote before being
  written, the standard mitigation, since Excel/Sheets/LibreOffice
  all treat those as "this cell is a formula" otherwise (e.g. a
  malicious `=cmd|'/c calc'!A1` value in the original upload would
  otherwise execute when the cleaned file is later opened by anyone
  in a spreadsheet app). Numeric/boolean columns are never touched.
  One side effect: phone numbers stored in international format
  (leading `+`) get the same prefix, since `+` is a formula trigger
  too, Excel hides that leading quote automatically (it's the
  standard "force text" marker), but it will be visible if the CSV
  is read as raw text or re-parsed by another script instead of
  opened in a spreadsheet app.

Still open, not yet addressed:

- The `job_id` (a UUID4) is the only thing scoping access to a
  specific upload, anyone who obtains a valid API key can act on
  any job whose id they have or guess. Fine for a small trusted team,
  not a substitute for per-user accounts if you need to isolate
  different users' data from each other.
- `ALLOWED_ORIGINS` still defaults to `*` (see `config.py`), set it
  to your actual frontend origin(s) in production.

## What Omixa does and doesn't touch

Prompted by early testing feedback (see below), a few explicit rules
about scope:

- **Header row: your call, not Omixa's.** Omixa never decides on its
  own whether row 1 is a header. `/api/process` and `/api/report`
  both take an optional `has_header` (default `true`); set it to
  `false` and row 1 is treated as ordinary data, with generic column
  names (`column_1`, `column_2`, ...) instead of real headers. The
  `/clean` workspace exposes this as a checkbox.
- **Multiple sheets survive.** If an uploaded `.xlsx` has more than
  one sheet, only the sheet Omixa actually reads and cleans (the
  first one) is rebuilt, every other sheet is carried through to the
  downloaded file exactly as it was in the source, instead of
  disappearing. `.xls` sources can't do this (openpyxl can't open
  `.xls` at all, see `processing/pipeline.py`'s `_first_sheet_name`),
  so a multi-sheet `.xls` upload still only round-trips its first
  sheet.
- **Formatting on the cleaned sheet itself is not preserved.**
  Omixa only ever changes cell *values*, it never explicitly sets a
  font, size, bold/italic state, or column width. But the sheet it
  writes is rebuilt from the cleaned data rather than edited in
  place, so it comes out in the export library's plain defaults
  regardless of what the original file's fonts/sizes/bold/italic/
  column widths were. Keeping the original per-cell styling on the
  cleaned sheet itself (not just the untouched other sheets) would
  mean editing the source workbook's cells in place instead of
  rebuilding the sheet, a bigger change than what's done here.

## Cleaning rules

`cleaning/rules.py` runs an ordered pipeline of auto-fix rules
(`cleaning.rules.DEFAULT_RULES`), each safe enough to apply without
asking first:

1. **column_names**, tidy headers into consistent `snake_case`
2. **formatting**, trim/collapse whitespace
3. **missing_token_normalization**, treat `"N/A"`, `"null"`, `"-"`, blanks, etc. as real missing values
4. **numeric_text_cleaning**, strip `$`, `,`, `%` from numbers stored as text and convert dtype (whole-column-safe only)
5. **boolean_standardization**, `Yes/No`, `True/False`, `Y/N` → real booleans (never touches `1`/`0`)
6. **categorical_standardization**, merges case/whitespace-only variants (`Male`/`MALE`/`male`)
7. **email_cleaning**, trims + lowercases email-shaped columns
8. **phone_cleaning**, collapses accidental repeated punctuation only, in phone-shaped columns
9. **date_standardization**, normalizes dates to `YYYY-MM-DD`, but only when day-first vs month-first parsing agree (unambiguous)
10. **missing_values**, median (or mode, for booleans) for numbers, `"Unknown"` for text
11. **duplicates**, drops exact duplicate rows, keeping the first

Anything too ambiguous to fix safely (mismatched date formats,
inconsistent categories that aren't just case variants, outliers,
constant columns, malformed emails/phones, likely near-duplicate
records, mixed-type columns) is never silently changed, it's
surfaced instead via `cleaning/quality_report.py`, both before
cleaning (`GET /api/report/<job_id>`) and after
(`summary.quality_report` from `POST /api/process/<job_id>`), so the
user can decide what to do about it themselves.

Both the fixer and the detector share the same "is this an email
column? a phone column? a date column?" heuristics from
`cleaning/detectors.py`, so the report never promises a fix the rules
don't actually perform.
