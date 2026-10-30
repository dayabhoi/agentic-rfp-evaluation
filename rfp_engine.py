import io
import json
import sqlite3
from datetime import datetime, timezone

import fitz
from pydantic import BaseModel, Field, ValidationError, field_validator

def default_criteria():
    return [
        {"criterion_id": 1, "name": "Technical Capability", "description": "Architecture, integrations, scalability, and technical fit.", "weight": 30.0, "max_score": 10, "is_active": True},
        {"criterion_id": 2, "name": "Implementation Plan", "description": "Timeline, milestones, staffing, dependencies, and risk plan.", "weight": 20.0, "max_score": 10, "is_active": True},
        {"criterion_id": 3, "name": "Commercial Value", "description": "Pricing clarity, total cost, assumptions, and value for money.", "weight": 20.0, "max_score": 10, "is_active": True},
        {"criterion_id": 4, "name": "Security & Compliance", "description": "Security controls, certifications, privacy, and auditability.", "weight": 20.0, "max_score": 10, "is_active": True},
        {"criterion_id": 5, "name": "Support & Experience", "description": "Support model, similar projects, references, and relevant experience.", "weight": 10.0, "max_score": 10, "is_active": True},
    ]

def connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS evaluation_criteria (
            criterion_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL,
            max_score INTEGER NOT NULL DEFAULT 10,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS rfp_runs (
            rfp_run_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS supplier_results (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rfp_run_id TEXT NOT NULL,
            supplier_name TEXT NOT NULL,
            submission_date TEXT NOT NULL,
            experience_rating INTEGER NOT NULL,
            absolute_score REAL NOT NULL,
            ppi REAL NOT NULL,
            final_rank INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            FOREIGN KEY(rfp_run_id) REFERENCES rfp_runs(rfp_run_id)
        );
        """)
        count = conn.execute("SELECT COUNT(*) FROM evaluation_criteria").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO evaluation_criteria(criterion_id,name,description,weight,max_score,is_active) VALUES(?,?,?,?,?,?)",
                [(c["criterion_id"], c["name"], c["description"], c["weight"], c["max_score"], int(c["is_active"])) for c in default_criteria()]
            )

def get_criteria(db_path):
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM evaluation_criteria ORDER BY criterion_id").fetchall()
    return [{**dict(r), "is_active": bool(r["is_active"])} for r in rows]

def save_criteria(db_path, records):
    with connect(db_path) as conn:
        conn.execute("DELETE FROM evaluation_criteria")
        conn.executemany(
            "INSERT INTO evaluation_criteria(criterion_id,name,description,weight,max_score,is_active) VALUES(?,?,?,?,?,?)",
            [(int(c["criterion_id"]), str(c["name"]).strip(), str(c.get("description", "")), float(c["weight"]), int(c["max_score"]), int(bool(c["is_active"]))) for c in records]
        )

def create_run(db_path, run_id):
    with connect(db_path) as conn:
        conn.execute("INSERT INTO rfp_runs(rfp_run_id,created_at,status) VALUES(?,?,?)",
                     (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), "running"))

def extract_pdf_text(pdf_bytes):
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n\n".join(page.get_text("text") for page in doc)

class CriterionResult(BaseModel):
    criterion_id: int
    score: float
    max_score: float
    justification: str = ""
    evidence: str = ""

class EvaluationResult(BaseModel):
    supplier_name: str = ""
    criteria: list[CriterionResult] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    overall_summary: str = ""

    @field_validator("risks", mode="before")
    @classmethod
    def risks_list(cls, value):
        if value is None: return []
        if isinstance(value, str): return [value]
        return value

def evaluate_proposal(client, model, supplier_name, document_text, criteria):
    criterion_spec = [{
        "criterion_id": int(c["criterion_id"]), "name": c["name"],
        "description": c["description"], "max_score": int(c["max_score"])
    } for c in criteria]
    # Keep enough text for evaluation while preventing very large prompts.
    text = document_text[:70000]
    prompt = f"""
You are an evidence-grounded supplier proposal evaluator. Evaluate ONLY the proposal text supplied below.
Do not infer certifications, prices, capabilities, or references not explicitly supported by the text.
Return JSON only with this schema:
{{
  "supplier_name": "{supplier_name}",
  "criteria": [
    {{"criterion_id": 1, "score": 0, "max_score": 10, "justification": "short reason", "evidence": "quote or precise location/description from proposal"}}
  ],
  "risks": ["risk grounded in proposal or missing information"],
  "overall_summary": "brief summary"
}}
Return exactly one criterion result for every criterion in this list, using the supplied criterion_id and max_score. Score each from 0 to max_score inclusive. Missing information should not be assumed; reflect it in score, justification, and risks. Evidence must be traceable to the document. Do not calculate final weighted scores, benchmarks, PPI, or rankings.
ACTIVE CRITERIA:
{json.dumps(criterion_spec, ensure_ascii=False)}
SUPPLIER: {supplier_name}
PROPOSAL TEXT:
{text}
"""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return one valid JSON object only. Do not use markdown fences."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0
    )
    return json.loads(response.choices[0].message.content)

def normalize_evaluation(raw, criteria):
    warnings = []
    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
        parsed = EvaluationResult.model_validate(raw or {})
        incoming = {int(c.criterion_id): c for c in parsed.criteria}
        clean = []
        for criterion in criteria:
            cid = int(criterion["criterion_id"])
            max_score = float(criterion["max_score"])
            result = incoming.get(cid)
            if result is None:
                warnings.append(f"Missing result for {criterion['name']}; score normalized to 0.")
                clean.append({
                    "criterion_id": cid, "name": criterion["name"], "score": 0.0,
                    "max_score": max_score, "weight": float(criterion["weight"]),
                    "justification": "No valid criterion result was returned.", "evidence": ""
                })
                continue
            score = float(result.score)
            if score < 0 or score > max_score:
                clipped = min(max(score, 0.0), max_score)
                warnings.append(f"{criterion['name']}: score {score} was clipped to {clipped}.")
                score = clipped
            if abs(float(result.max_score) - max_score) > 0.001:
                warnings.append(f"{criterion['name']}: returned maximum score did not match configured maximum; configured maximum used.")
            clean.append({
                "criterion_id": cid, "name": criterion["name"], "score": score,
                "max_score": max_score, "weight": float(criterion["weight"]),
                "justification": result.justification or "", "evidence": result.evidence or ""
            })
        extra = set(incoming) - {int(c["criterion_id"]) for c in criteria}
        if extra:
            warnings.append(f"Ignored unknown criterion IDs: {sorted(extra)}.")
        return ({
            "supplier_name": parsed.supplier_name,
            "criteria": clean, "risks": parsed.risks,
            "overall_summary": parsed.overall_summary, "warnings": warnings
        }, warnings)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        warnings.append(f"Malformed LLM response; all scores normalized to 0 ({type(exc).__name__}).")
        return ({
            "supplier_name": "", "criteria": [{
                "criterion_id": int(c["criterion_id"]), "name": c["name"], "score": 0.0,
                "max_score": float(c["max_score"]), "weight": float(c["weight"]),
                "justification": "Invalid or missing LLM result.", "evidence": ""
            } for c in criteria],
            "risks": ["Evaluation output could not be validated."],
            "overall_summary": "No valid assessment was available.", "warnings": warnings
        }, warnings)

def calculate_rankings(items, criteria):
    if not items:
        return []
    # Copy mutable structures so callers' original objects are not modified.
    prepared = []
    for item in items:
        ev = json.loads(json.dumps(item["evaluation"]))
        total = 0.0
        for c in ev["criteria"]:
            total += (float(c["score"]) / float(c["max_score"])) * (float(c["weight"]) / 100.0) * 10.0
        ev["absolute_score"] = total
        prepared.append({**item, "evaluation": ev, "absolute_score": total})
    benchmarks = {}
    for criterion in criteria:
        cid = int(criterion["criterion_id"])
        scores = [next(c["score"] for c in p["evaluation"]["criteria"] if c["criterion_id"] == cid) for p in prepared]
        benchmarks[cid] = max(scores) if scores else 0.0
    for p in prepared:
        weighted_relative = 0.0
        for c in p["evaluation"]["criteria"]:
            benchmark = benchmarks[c["criterion_id"]]
            relative = (float(c["score"]) / benchmark * 100.0) if benchmark > 0 else 100.0
            c["benchmark_score"] = benchmark
            c["gap"] = round(float(c["score"]) - benchmark, 6)
            c["relative_performance_pct"] = round(relative, 6)
            weighted_relative += relative * (float(c["weight"]) / 100.0)
        p["ppi"] = round(weighted_relative, 6)
        p["evaluation"]["ppi"] = p["ppi"]
        p["evaluation"]["absolute_score"] = round(p["absolute_score"], 6)
    prepared.sort(key=lambda p: (
        -p["ppi"], p["submission_date"], -int(p["experience_rating"]), p["supplier_name"].casefold()
    ))
    for rank, p in enumerate(prepared, start=1):
        p["final_rank"] = rank
        p["evaluation"]["final_rank"] = rank
        p["evaluation"]["submission_date"] = p["submission_date"]
        p["evaluation"]["experience_rating"] = int(p["experience_rating"])
        p["evaluation"]["warnings"] = p.get("warnings", [])
    return prepared

def persist_results(db_path, run_id, ranked, criteria, status="completed"):
    with connect(db_path) as conn:
        for p in ranked:
            payload = p["evaluation"]
            conn.execute("""
                INSERT INTO supplier_results
                (rfp_run_id,supplier_name,submission_date,experience_rating,absolute_score,ppi,final_rank,result_json)
                VALUES(?,?,?,?,?,?,?,?)
            """, (run_id, p["supplier_name"], p["submission_date"], int(p["experience_rating"]),
                  float(p["absolute_score"]), float(p["ppi"]), int(p["final_rank"]),
                  json.dumps(payload, ensure_ascii=False)))
        conn.execute("UPDATE rfp_runs SET status=? WHERE rfp_run_id=?", (status, run_id))

def get_run_history(db_path):
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM rfp_runs ORDER BY created_at DESC").fetchall()]

def get_run_results(db_path, run_id):
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM supplier_results WHERE rfp_run_id=? ORDER BY final_rank", (run_id,)
        ).fetchall()]
