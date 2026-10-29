"""TalentVerse Python backend with Gemini structured output and a local JSON vault."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA_DIR = ROOT / "data"
DB_FILE = DATA_DIR / "talentverse.json"


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()
PORT = int(os.environ.get("PORT", "3000"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash-lite"

SCHEMAS = {
    "profile": {
        "required": ["name", "headline", "skills", "experience"],
        "fields": ["name", "headline", "skills", "experience", "education", "yearsExperience"],
    },
    "gap": {
        "required": ["goalRole", "currentSkills", "skillGaps", "milestones"],
        "fields": ["goalRole", "currentSkills", "skillGaps", "milestones", "timeHorizon"],
    },
    "match": {
        "required": ["roleTitle", "requirements", "matches", "gaps", "fitScore"],
        "fields": ["roleTitle", "requirements", "matches", "gaps", "fitScore"],
    },
    "portfolio": {
        "required": ["title", "problem", "skills", "deliverables"],
        "fields": ["title", "problem", "skills", "deliverables", "stretchGoal"],
    },
    "interview": {
        "required": ["strengths", "improvementAreas", "nextPracticeTasks"],
        "fields": ["strengths", "improvementAreas", "nextPracticeTasks", "confidence"],
    },
}

GEMINI_SCHEMAS = {
    "profile": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "headline": {"type": "STRING"},
        "skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "experience": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "title": {"type": "STRING"}, "summary": {"type": "STRING"}
        }, "required": ["title", "summary"]}},
        "education": {"type": "STRING"}, "yearsExperience": {"type": "INTEGER", "minimum": 0}
    }, "required": ["name", "headline", "skills", "experience"]},
    "gap": {"type": "OBJECT", "properties": {
        "goalRole": {"type": "STRING"}, "currentSkills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "skillGaps": {"type": "ARRAY", "items": {"type": "STRING"}}, "timeHorizon": {"type": "STRING"},
        "milestones": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "phase": {"type": "STRING"}, "focus": {"type": "STRING"}, "outcome": {"type": "STRING"}
        }, "required": ["phase", "focus", "outcome"]}}
    }, "required": ["goalRole", "currentSkills", "skillGaps", "milestones"]},
    "match": {"type": "OBJECT", "properties": {
        "roleTitle": {"type": "STRING"}, "requirements": {"type": "ARRAY", "items": {"type": "STRING"}},
        "matches": {"type": "ARRAY", "items": {"type": "STRING"}}, "gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        "fitScore": {"type": "NUMBER", "minimum": 0, "maximum": 100}
    }, "required": ["roleTitle", "requirements", "matches", "gaps", "fitScore"]},
    "portfolio": {"type": "OBJECT", "properties": {
        "title": {"type": "STRING"}, "problem": {"type": "STRING"},
        "skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "deliverables": {"type": "ARRAY", "items": {"type": "STRING"}}, "stretchGoal": {"type": "STRING"}
    }, "required": ["title", "problem", "skills", "deliverables"]},
    "interview": {"type": "OBJECT", "properties": {
        "strengths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "improvementAreas": {"type": "ARRAY", "items": {"type": "STRING"}},
        "nextPracticeTasks": {"type": "ARRAY", "items": {"type": "STRING"}},
        "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 100}
    }, "required": ["strengths", "improvementAreas", "nextPracticeTasks"]},
}

SKILL_BANK = [
    "JavaScript", "TypeScript", "React", "Python", "SQL", "Excel", "Tableau", "Power BI", "Figma",
    "communication", "product sense", "A/B testing", "statistics", "data visualization",
    "stakeholder storytelling", "Node.js", "Git",
]


def ensure_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        DB_FILE.write_text('{"records": []}', encoding="utf-8")


def read_db() -> dict[str, Any]:
    ensure_db()
    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("records"), list) else {"records": []}
    except (OSError, json.JSONDecodeError):
        return {"records": []}


def write_db(data: dict[str, Any]) -> None:
    ensure_db()
    temp = DB_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(DB_FILE)


def skills_in(text: str) -> list[str]:
    lowered = text.lower()
    return list(dict.fromkeys(skill for skill in SKILL_BANK if skill.lower() in lowered))


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?]", text) if part.strip()]


def first_sentence(text: str, fallback: str) -> str:
    return sentences(text)[0] if sentences(text) else fallback


def title_from(text: str, fallback: str) -> str:
    value = re.sub(r"^(i|we|my|our)\s+(want|am|are|enjoy|like|love)\s+", "", first_sentence(text, fallback), flags=re.I)
    words = value.split()[:5]
    return " ".join(word.capitalize() if len(word) > 3 else word.lower() for word in words) or fallback


def demo_extract(kind: str, text: str) -> dict[str, Any]:
    skills = skills_in(text)
    if kind == "profile":
        match = re.search(r"(?:I am|I'm|name is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})", text, re.I)
        return {
            "name": match.group(1) if match else "Candidate profile",
            "headline": "Computer science student building useful digital products" if "student" in text.lower() else "Emerging technology professional",
            "skills": skills or ["Python", "communication"],
            "experience": [{"title": "Project / internship experience", "summary": first_sentence(text, "Built practical projects with measurable outcomes.")}],
            "education": "Computer Science" if "student" in text.lower() else "Not specified",
            "yearsExperience": int(re.search(r"(\d+)\s+years?", text, re.I).group(1)) if re.search(r"(\d+)\s+years?", text, re.I) else 0,
        }
    if kind == "gap":
        match = re.search(r"(?:become|be a|target|goal(?: is)?|for)\s+(?:a|an)?\s*([a-z][a-z ]{3,40}?)(?:\s+in the|\s+over the|\.|,|$)", text, re.I)
        current = skills or ["SQL", "Python", "Excel"]
        gaps = [item for item in ["Tableau", "experimentation", "stakeholder storytelling"] if item.lower() not in {x.lower() for x in current}]
        return {"goalRole": match.group(1).strip() if match else "Product Data Analyst", "currentSkills": current, "skillGaps": gaps, "timeHorizon": "6 months", "milestones": [
            {"phase": "Weeks 1-4", "focus": gaps[0] if gaps else "Core tools", "outcome": "Complete one guided project and publish a short reflection."},
            {"phase": "Weeks 5-12", "focus": gaps[1] if len(gaps) > 1 else "Applied practice", "outcome": "Run an end-to-end case study with evidence."},
            {"phase": "Weeks 13-24", "focus": gaps[2] if len(gaps) > 2 else "Portfolio readiness", "outcome": "Share a portfolio project and complete two mock interviews."},
        ]}
    if kind == "match":
        requirements = ["SQL", "dashboards", "A/B testing", "Python", "communication", "product sense"]
        matches = [item for item in requirements if item.lower() in text.lower()]
        role = re.search(r"hiring\s+(?:for\s+|a\s+|an\s+)?([^\.\n]+)", text, re.I)
        return {"roleTitle": role.group(1).strip() if role else "Junior Product Analyst", "requirements": requirements, "matches": matches, "gaps": [item for item in requirements if item not in matches], "fitScore": round(len(matches) / len(requirements) * 100)}
    if kind == "portfolio":
        lines = sentences(text)
        chosen = skills or ["problem solving", "research", "communication"]
        topic = title_from(text, "Your Interest Project")
        problem = next((line for line in lines if re.search(r"problem|need|help|improve|lack|challenge", line, re.I)), f"People interested in {topic.lower()} need a clearer way to make progress.")
        return {"title": f"{topic} Lab", "problem": problem, "skills": chosen, "deliverables": [f"A focused {chosen[0]} prototype", "A short research and user-insight log", f"A working demo that shows {' and '.join(chosen[:2])}", "README with decisions, evidence and outcomes"], "stretchGoal": f"Add one measurable outcome that proves whether the {topic.lower()} idea is useful."}
    lines = sentences(text)
    positive = [line for line in lines if re.search(r"strength|clear|good|strong|success|result|led|built|improved|collaborat", line, re.I)]
    negative = [line for line in lines if re.search(r"weak|rambl|unclear|difficult|conflict|should|did not|didn't|miss|nervous|struggl", line, re.I)]
    strengths = [(line[:87] + "...") if len(line) > 90 else line for line in (positive[:3] or ["Shows willingness to reflect on interview performance"])]
    improvement = [(line[:87] + "...") if len(line) > 90 else line for line in (negative[:3] or ["Add one concrete example to each answer", "Quantify the outcome of your work"])]
    return {"strengths": strengths, "improvementAreas": improvement, "nextPracticeTasks": [f"Practice one concise answer that addresses: {area}" for area in improvement], "confidence": min(95, max(45, 55 + len(strengths) * 8 - len(improvement) * 2))}


def validate(kind: str, data: Any) -> dict[str, Any]:
    schema = SCHEMAS.get(kind)
    if not schema or not isinstance(data, dict):
        return {"valid": False, "errors": ["The structured result is not an object."]}
    errors = [f"{key} is required" for key in schema["required"] if data.get(key) is None or data.get(key) == ""]
    for key in ["skills", "experience", "currentSkills", "skillGaps", "milestones", "requirements", "matches", "gaps", "strengths", "improvementAreas", "nextPracticeTasks", "deliverables"]:
        if key in data and not isinstance(data[key], list):
            errors.append(f"{key} must be an array")
    if kind == "match" and (not isinstance(data.get("fitScore"), (int, float)) or not 0 <= data["fitScore"] <= 100):
        errors.append("fitScore must be a number from 0 to 100")
    return {"valid": not errors, "errors": errors}


def gemini_extract(kind: str, text: str) -> tuple[dict[str, Any], str]:
    prompt = ("You are TalentVerse, a career intelligence assistant. Extract only facts supported by the user's input. "
              "Return JSON matching the supplied schema. Do not invent employers, dates, skills, achievements, or metrics. "
              "For missing string values use 'Not specified'; for missing list values use an empty list. "
              f"Analysis type: {kind}. User input:\n{text}")
    payload = {"systemInstruction": {"parts": [{"text": "You produce concise, accurate, structured career insights."}]}, "contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": GEMINI_SCHEMAS[kind]}}
    models = list(dict.fromkeys([GEMINI_MODEL, GEMINI_FALLBACK_MODEL]))
    last_busy_error = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="ignore")
            if error.code in {404, 429, 500, 503}:
                last_busy_error = f"Gemini model {model} is temporarily unavailable ({error.code})."
                continue
            if error.code in {400, 401, 403}:
                raise RuntimeError("Gemini rejected the request. Check the API key, model access, and billing settings.") from RuntimeError(detail)
            raise RuntimeError(f"Gemini request failed ({error.code}).") from RuntimeError(detail)
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError("Gemini could not be reached. Check your network connection.") from error
        parts = raw.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        content = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not content:
            raise RuntimeError("Gemini returned an empty response.")
        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Gemini returned invalid JSON.") from error
        checked = validate(kind, result)
        if not checked["valid"]:
            raise RuntimeError("Gemini returned a result that did not match the TalentVerse schema.")
        return result, model
    raise RuntimeError(last_busy_error or "Gemini is temporarily unavailable. Please try again.")


def extract(kind: str, text: str) -> tuple[dict[str, Any], str, str | None]:
    if GEMINI_API_KEY:
        try:
            result, model = gemini_extract(kind, text)
            return result, f"Gemini / {model}", None
        except RuntimeError as error:
            return demo_extract(kind, text), "Local demo fallback", str(error)
    return demo_extract(kind, text), "Local demo mode", "Add GEMINI_API_KEY to .env to enable Gemini extraction."


class TalentVerseHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api"):
            super().log_message(format, *args)

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Input is too large.")
        raw = self.rfile.read(length).decode("utf-8")
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self.send_json(200, {"ok": True, "database": "json-file", "provider": f"Gemini / {GEMINI_MODEL}" if GEMINI_API_KEY else "Local demo mode", "aiConfigured": bool(GEMINI_API_KEY)})
        elif path == "/api/records":
            records = read_db()["records"]
            self.send_json(200, {"records": list(reversed(records))})
        else:
            super().do_GET()

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            body = self.read_json()
            if path == "/api/analyze":
                kind = str(body.get("type", ""))
                text = str(body.get("text", "")).strip()
                if kind not in SCHEMAS:
                    return self.send_json(400, {"error": "Choose a supported analysis type."})
                if len(text) < 20:
                    return self.send_json(422, {"error": "Please provide at least 20 characters so TalentVerse can extract meaningful fields."})
                result, provider, warning = extract(kind, text)
                validation = validate(kind, result)
                status = 200 if validation["valid"] else 422
                return self.send_json(status, {"type": kind, "input": text, "result": result, "validation": validation, "provider": provider, "warning": warning, "schema": SCHEMAS[kind]})
            if path == "/api/records":
                kind = str(body.get("type", ""))
                result = body.get("result", {})
                validation = validate(kind, result)
                if not validation["valid"]:
                    return self.send_json(422, {"error": "Record failed validation.", "validation": validation})
                db = read_db()
                record = {"id": str(uuid.uuid4()), "type": kind, "result": result, "sourcePreview": str(body.get("source", ""))[:160], "createdAt": datetime.now(timezone.utc).isoformat()}
                db["records"].append(record)
                write_db(db)
                return self.send_json(201, {"saved": True, "record": record})
            return self.send_json(404, {"error": "Route not found."})
        except json.JSONDecodeError:
            return self.send_json(400, {"error": "Please send valid JSON."})
        except ValueError as error:
            return self.send_json(422, {"error": str(error)})
        except Exception:
            return self.send_json(500, {"error": "The server could not complete that request. Please try again."})


if __name__ == "__main__":
    ensure_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), TalentVerseHandler)
    print(f"TalentVerse Python server running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTalentVerse server stopped.")
    finally:
        server.server_close()
