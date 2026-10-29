# TalentVerse — Skills & Career Intelligence

TalentVerse is a full-stack CSB312 Mini Project 2 implementation for Option 9. It accepts text input and turns it into a validated, schema-guided career insight. The frontend previews the structured result, then saves an approved record to the backend's local database file.

## Run locally

```bash
python3 server.py
```

Open http://localhost:3000.

Copy `.env.example` to `.env` and add your Gemini key. The key is read only by the Python backend and is never sent to the browser.

```bash
cp .env.example .env
python3 server.py
```

If Gemini is unavailable, the app stays usable in local demo mode and shows a clear provider warning instead of breaking the presentation.

## Demonstrated workflow

`Text input → schema-guided extraction → validation → POST /api/records → data/talentverse.json → frontend confirmation`

The five lenses map to the five TalentVerse automation ideas: profile builder, skill-gap map, role match, portfolio brief, and interview debrief. Short or missing input is rejected with a 422-style validation message, and all saves require an explicit preview/save action.

## API

- `GET /api/health` — service/database status
- `GET /api/records` — saved insights
- `POST /api/analyze` — `{ type, text }`
- `POST /api/records` — `{ type, result, source }`

## Technology and design notes

- Frontend: semantic HTML, CSS, vanilla JavaScript.
- Backend: Python standard-library `http.server`.
- Database: atomic JSON-file persistence in `data/talentverse.json`.
- AI: Gemini `generateContent` with `responseMimeType: application/json` and a per-mode `responseSchema`, plus a validated local fallback.
- Safeguard: analysis is shown as a reviewable preview before a record is persisted.
