import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from openai import OpenAI

from rfp_engine import (
    init_db, get_criteria, save_criteria, create_run, extract_pdf_text,
    evaluate_proposal, calculate_rankings, persist_results, get_run_history,
    get_run_results, default_criteria, normalize_evaluation
)

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "data" / "rfp_evaluation.db"

st.set_page_config(page_title="Agentic RFP Evaluator", page_icon="📊", layout="wide")
init_db(DB_PATH)

st.title("📊 Agentic RFP Evaluation & Supplier Ranking")
st.caption("Evidence-grounded proposal review · deterministic scoring · explainable rankings")

with st.sidebar:
    st.header("LLM configuration")
    provider = st.selectbox("API provider", ["OpenAI", "OpenAI-compatible endpoint"])
    api_key = st.text_input("API key", type="password", help="Used only for this session; do not paste it into source code.")
    model = st.text_input("Model", value="gpt-4o-mini")
    base_url = None
    if provider == "OpenAI-compatible endpoint":
        base_url = st.text_input("API base URL", placeholder="https://your-provider.example/v1")
    st.caption("Your key is not saved to SQLite or exported. The selected provider may process proposal text.")

    st.divider()
    page = st.radio("Navigate", ["Evaluate suppliers", "Leaderboard & run details", "Evaluation criteria", "About & deployment"])

def get_client():
    if not api_key:
        st.warning("Enter your API key in the sidebar before running an LLM evaluation.")
        return None
    kwargs = {"api_key": api_key}
    if provider == "OpenAI-compatible endpoint":
        if not base_url:
            st.warning("Enter the API base URL for the compatible endpoint.")
            return None
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)

if page == "Evaluate suppliers":
    st.subheader("1. Evaluation criteria")
    criteria = get_criteria(DB_PATH)
    if not criteria:
        st.info("No active criteria found. Open Evaluation criteria to restore defaults.")
    active = [c for c in criteria if c["is_active"]]
    total_weight = sum(float(c["weight"]) for c in active)
    c1, c2, c3 = st.columns(3)
    c1.metric("Active criteria", len(active))
    c2.metric("Total active weight", f"{total_weight:.1f}%")
    c3.metric("Maximum score per criterion", "10")
    if abs(total_weight - 100) > 0.001:
        st.error("Active criteria weights must total 100% before evaluation.")

    st.subheader("2. Upload supplier proposals")
    st.write("Upload one or more PDF proposals. Add the matching supplier name, submission date, and historical experience rating for each file.")
    uploads = st.file_uploader("Supplier RFP PDFs", type=["pdf"], accept_multiple_files=True)
    entries = []
    if uploads:
        for i, uploaded in enumerate(uploads):
            with st.expander(f"Supplier {i+1}: {uploaded.name}", expanded=True):
                col1, col2, col3 = st.columns(3)
                default_name = Path(uploaded.name).stem.replace("_", " ").replace("-", " ")
                supplier_name = col1.text_input("Supplier name", value=default_name, key=f"name_{i}")
                submission_date = col2.date_input("Submission date", value=date.today(), key=f"date_{i}")
                experience = col3.slider("Historical experience rating", 0, 10, 5, key=f"exp_{i}")
                entries.append({
                    "file": uploaded, "supplier_name": supplier_name.strip(),
                    "submission_date": submission_date.isoformat(),
                    "experience_rating": int(experience)
                })

    st.subheader("3. Run evaluation")
    st.caption("The same validated scorecards produce the same calculated scores and ordering. LLM judgments can vary between runs.")
    if st.button("🚀 Evaluate suppliers", type="primary", disabled=not uploads):
        if not api_key:
            st.error("Please enter your API key in the sidebar.")
        elif not active:
            st.error("There are no active evaluation criteria.")
        elif abs(total_weight - 100) > 0.001:
            st.error("Active criteria weights must total exactly 100%. Update them in Evaluation criteria.")
        elif any(not x["supplier_name"] for x in entries):
            st.error("Every uploaded PDF needs a supplier name.")
        elif len({x["supplier_name"].casefold() for x in entries}) != len(entries):
            st.error("Supplier names must be unique within a batch.")
        else:
            client = get_client()
            if client:
                run_id = str(uuid.uuid4())
                create_run(DB_PATH, run_id)
                output_items = []
                progress = st.progress(0)
                status = st.empty()
                for idx, entry in enumerate(entries):
                    status.info(f"Evaluating {entry['supplier_name']} ({idx+1}/{len(entries)})…")
                    try:
                        raw_bytes = entry["file"].getvalue()
                        extracted = extract_pdf_text(raw_bytes)
                        if len(extracted.strip()) < 40:
                            raise ValueError("Very little text was extracted. The PDF may be scanned; OCR is not included.")
                        evaluation = evaluate_proposal(
                            client=client, model=model, supplier_name=entry["supplier_name"],
                            document_text=extracted, criteria=active
                        )
                        normalized, warnings = normalize_evaluation(evaluation, active)
                        output_items.append({
                            **entry, "supplier_name": entry["supplier_name"],
                            "evaluation": normalized, "warnings": warnings,
                            "document_text_length": len(extracted)
                        })
                    except Exception as exc:
                        st.error(f"{entry['supplier_name']}: evaluation failed — {exc}")
                        output_items.append({
                            **entry, "supplier_name": entry["supplier_name"],
                            "evaluation": normalize_evaluation({}, active)[0],
                            "warnings": [f"Evaluation error: {type(exc).__name__}: {exc}"],
                            "document_text_length": 0
                        })
                    progress.progress((idx+1)/len(entries))
                status.success("Evaluation and validation complete.")
                ranked = calculate_rankings(output_items, active)
                persist_results(DB_PATH, run_id, ranked, active, status="completed")
                st.session_state["last_run_id"] = run_id
                st.session_state["last_run_results"] = ranked
                st.success(f"Saved run {run_id}")
                st.rerun()

elif page == "Leaderboard & run details":
    st.subheader("Leaderboard & run details")
    history = get_run_history(DB_PATH)
    if not history:
        st.info("No completed runs yet. Evaluate supplier PDFs first.")
    else:
        labels = {f"{r['created_at']} · {r['rfp_run_id']} · {r['status']}": r["rfp_run_id"] for r in history}
        selected_label = st.selectbox("Select evaluation run", list(labels.keys()))
        run_id = labels[selected_label]
        results = get_run_results(DB_PATH, run_id)
        if results:
            rows = []
            for r in results:
                rows.append({
                    "Rank": r["final_rank"], "Supplier": r["supplier_name"],
                    "Absolute score / 10": round(r["absolute_score"], 3),
                    "PPI (%)": round(r["ppi"], 2), "Submission date": r["submission_date"],
                    "Experience rating": r["experience_rating"]
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"RFP_RUN_ID: {run_id}")
            st.info("Tie-break order: higher PPI → earlier submission date → higher historical experience rating → supplier name ascending.")
            selected_supplier = st.selectbox("View detailed scorecard", [r["supplier_name"] for r in results])
            record = next(r for r in results if r["supplier_name"] == selected_supplier)
            payload = record["result_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            st.subheader(f"Rank #{record['final_rank']} · {record['supplier_name']}")
            m1, m2, m3 = st.columns(3)
            m1.metric("Absolute weighted score", f"{record['absolute_score']:.3f} / 10")
            m2.metric("Peer Performance Index", f"{record['ppi']:.2f}%")
            m3.metric("Experience rating", f"{record['experience_rating']} / 10")
            crit_rows = []
            for c in payload.get("criteria", []):
                crit_rows.append({
                    "Criterion": c.get("name", f"Criterion {c.get('criterion_id')}"),
                    "Score": c.get("score"), "Max": c.get("max_score"),
                    "Weight (%)": c.get("weight"),
                    "Benchmark score": c.get("benchmark_score"),
                    "Gap": c.get("gap"),
                    "Relative performance (%)": c.get("relative_performance_pct")
                })
            st.dataframe(pd.DataFrame(crit_rows), use_container_width=True, hide_index=True)
            for c in payload.get("criteria", []):
                with st.expander(f"{c.get('name', 'Criterion')} · {c.get('score')}/{c.get('max_score')}"):
                    st.write("**Justification**")
                    st.write(c.get("justification") or "No justification returned.")
                    st.write("**Supporting evidence**")
                    st.write(c.get("evidence") or "No evidence returned.")
            st.write("**Overall summary**")
            st.write(payload.get("overall_summary", ""))
            st.write("**Risks**")
            risks = payload.get("risks", [])
            st.write("\n".join(f"- {r}" for r in risks) if risks else "No risks listed.")
            warnings = payload.get("warnings", [])
            if warnings:
                st.warning("Validation warnings:\n\n" + "\n".join(f"- {w}" for w in warnings))
            export = {
                "rfp_run_id": run_id,
                "supplier": record["supplier_name"],
                "submission_date": record["submission_date"],
                "experience_rating": record["experience_rating"],
                "absolute_score": record["absolute_score"],
                "ppi": record["ppi"], "final_rank": record["final_rank"],
                **payload
            }
            st.download_button("⬇️ Download selected supplier JSON", json.dumps(export, indent=2), file_name=f"{record['supplier_name'].replace(' ', '_')}_result.json", mime="application/json")
            all_export = {"rfp_run_id": run_id, "results": []}
            for r in results:
                p = r["result_json"] if isinstance(r["result_json"], dict) else json.loads(r["result_json"])
                all_export["results"].append({
                    "supplier_name": r["supplier_name"], "submission_date": r["submission_date"],
                    "experience_rating": r["experience_rating"], "absolute_score": r["absolute_score"],
                    "ppi": r["ppi"], "final_rank": r["final_rank"], **p
                })
            st.download_button("⬇️ Download complete run JSON", json.dumps(all_export, indent=2), file_name=f"rfp_run_{run_id}.json", mime="application/json")
        else:
            st.warning("This run has no persisted supplier results.")

elif page == "Evaluation criteria":
    st.subheader("Manage evaluation criteria")
    st.write("Edit the criteria in SQLite through this screen. Active criteria weights must total 100%.")
    criteria = get_criteria(DB_PATH)
    if not criteria:
        if st.button("Restore default criteria"):
            save_criteria(DB_PATH, default_criteria())
            st.rerun()
    else:
        df = pd.DataFrame(criteria)
        edited = st.data_editor(
            df, use_container_width=True, hide_index=True,
            column_config={
                "criterion_id": st.column_config.NumberColumn("ID", disabled=True),
                "name": st.column_config.TextColumn("Criterion"),
                "description": st.column_config.TextColumn("Description", width="large"),
                "weight": st.column_config.NumberColumn("Weight (%)", min_value=0.0, max_value=100.0, step=1.0),
                "max_score": st.column_config.NumberColumn("Max score", min_value=1, max_value=100, step=1),
                "is_active": st.column_config.CheckboxColumn("Active")
            },
            disabled=["criterion_id"]
        )
        active_weight = sum(float(row["weight"]) for _, row in edited.iterrows() if bool(row["is_active"]))
        st.metric("Active weight total", f"{active_weight:.1f}%")
        if st.button("Save criteria", type="primary"):
            if abs(active_weight - 100) > 0.001:
                st.error("Active criteria weights must total 100%.")
            elif edited["name"].astype(str).str.strip().eq("").any():
                st.error("Criterion names cannot be empty.")
            else:
                save_criteria(DB_PATH, edited.to_dict("records"))
                st.success("Criteria saved.")
                st.rerun()

else:
    st.subheader("About this project")
    st.markdown("""
**Workflow:** PDF extraction → LLM criterion evaluation → schema validation and normalization → deterministic scoring → peer benchmarking → ranking → SQLite persistence → JSON export.

**Scoring rules**
- Absolute weighted score = sum((criterion score / criterion max score) × criterion weight as a fraction).
- Criterion benchmark = highest supplier score for that criterion in the batch.
- Criterion gap = supplier score − benchmark score.
- Relative performance = score / benchmark × 100; when the benchmark is zero, relative performance is defined as 100% for all suppliers.
- PPI = weighted average of criterion relative-performance percentages.
- Tie-breaks: PPI descending, submission date ascending, experience rating descending, supplier name ascending.

**Limitations:** Text-based PDFs are supported; scanned PDFs require OCR before evaluation. LLM outputs can vary, but calculations and tie-break ordering are deterministic for validated scorecards. Review AI-generated assessments before making procurement decisions.
""")
    st.markdown("See `README.md` for local setup and Streamlit Community Cloud deployment.")
