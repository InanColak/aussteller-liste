# TODO — Deployment & Production Readiness

## User Interface: SharePoint + Power Automate

End users interact via SharePoint only — no terminal access required.

### Flow:
1. User drops a `.txt` file into `Eingabe/` folder (one URL per file)
2. Power Automate triggers → sends HTTP POST to backend API
3. Backend scrapes exhibitors → produces Excel/CSV
4. Power Automate saves result to `Ergebnisse/` folder
5. User receives a Teams notification

### Tasks:
- [x] FastAPI REST API (`POST /scrape`, `GET /status`, `GET /download`)
- [ ] Set up SharePoint folder structure
  - `Aussteller-Scraper/Eingabe/` — input .txt files
  - `Aussteller-Scraper/Ergebnisse/` — result Excel files
  - `Aussteller-Scraper/Fehler/` — error logs
- [ ] Create Power Automate flow
  - Trigger: SharePoint "When a file is created" → `Eingabe/`
  - Action: Read .txt file → parse URL
  - Action: HTTP POST → backend API
  - Action: Save result file to `Ergebnisse/`
  - Action: Send Teams notification (success/failure)
  - Action: Move processed .txt to `Archiv/`
- [ ] Define .txt file format standard (one URL per line)
- [ ] Send meaningful Teams message on error

---

## 1. FastAPI Backend API — DONE
- [x] `POST /scrape` — accept URL, start scrape, return job ID
- [x] `GET /scrape/{job_id}/status` — check job progress
- [x] `GET /scrape/{job_id}/download` — download result file
- [x] Async job queue with concurrency limiting (semaphore)
- [x] Health check endpoint (`GET /health`)

## 2. API Key Security — DONE
- [x] `.env` in `.gitignore`
- [x] Environment variables injected via Docker `env_file`

## 3. Rate Limiting & Cost Control — DONE
- [x] Daily scrape limit (default 50/day, configurable via `DAILY_SCRAPE_LIMIT`)
- [x] Concurrent job limit (default 3, configurable via `MAX_CONCURRENT_JOBS`)
- [x] Usage stats exposed via `/health` endpoint

## 4. Playwright & Headless Chromium — DONE
- [x] Docker image with Playwright + Chromium
- [x] Only Chromium included (no Firefox/WebKit)
- [x] Non-root user for container security
- [x] Memory limit (2GB) and CPU limit (1.5 cores) via docker-compose

## 5. Error Handling & Reliability — DONE
- [x] Structured logging (console + `scraper.log` file)
- [x] 10-minute timeout per scrape job
- [x] Meaningful error messages returned via API (timeout, no results, exceptions)
- [x] Zero-result detection (job marked as failed with explanation)

## 6. Output & Storage
- [ ] Results delivered to SharePoint `Ergebnisse/` via Power Automate
- [ ] File naming: `{FairName}_{Date}.xlsx`
- [ ] Retention policy for old files

## 7. Deploy
- [x] Dockerfile
- [x] Docker Compose config
- [x] Deploy to Hetzner server
- [x] DuckDNS domain: `ausstellerliste.duckdns.org`
- [x] nginx reverse proxy (port 8003 → scraper container)
- [x] SSL via certbot + Let's Encrypt (auto-renew)
- [ ] CI/CD pipeline (skipped for now — not needed with single developer)
- [ ] Rotate API key (current key was exposed in chat)

## 8. Copilot Studio + Power Automate Integration

Copilot Studio agent triggers a flow that calls the scraper API, saves result to SharePoint, and returns the URL to the user.

### Architecture:
```
User → Copilot Studio Agent → Flow → API (https://ausstellerliste.duckdns.org) → SharePoint → URL back to user
```

### Flow Steps (Copilot Studio Agent Flow):
- [x] Trigger: "Wenn ein Agent den Flow aufruft" — Input: `url` (Text)
- [x] HTTP POST `/scrape` — start scrape job (Header: X-API-Key, Body: url/format/limit)
- [x] JSON analysieren — extract `job_id` and `status`
- [ ] Wiederholen bis (Do Until) — poll until `status == completed` or `failed`
  - [ ] Verzögern (Delay) — 10 Sekunden
  - [ ] HTTP GET `/scrape/{job_id}/status` — check status
  - [ ] JSON analysieren — extract updated status, total_exhibitors, file_name
- [ ] Bedingung (Condition) — status == completed?
  - [ ] Wenn ja: HTTP GET `/scrape/{job_id}/download` — download file
  - [ ] Wenn ja: SharePoint "Datei erstellen" — save to `/Freigegebene Dokumente/Aussteller`
  - [ ] Wenn ja: Respond to agent — return SharePoint URL + total_exhibitors
  - [ ] Wenn nein: Respond to agent — return error message
- [ ] Configure Copilot Studio agent topic (trigger phrases, URL input, response message)
- [ ] End-to-end test
