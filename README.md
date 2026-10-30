# Agentic RFP Evaluation and Supplier Ranking

A Streamlit application based on the supplied mini-project brief. It reads supplier proposal PDFs, uses an LLM to score proposals against active SQLite criteria, validates scorecards, calculates deterministic weighted scores and peer metrics, ranks suppliers, persists results, and exports JSON.

## Requirements

- Python 3.10 or newer (Python 3.11 recommended)
- An API key for OpenAI or an OpenAI-compatible JSON-capable chat-completions endpoint
- VS Code (optional but recommended)

## Run locally in VS Code

1. Extract the ZIP and open the `agentic_rfp_evaluation` folder in VS Code.
2. Open the integrated terminal in that folder.
3. Create a virtual environment:

   **Windows**
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation, run:
   ```powershell
   .venv\Scripts\activate.bat
   ```

   **macOS/Linux**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install dependencies:
   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
5. Start the app:
   ```bash
   streamlit run app.py
   ```
6. In the sidebar, enter your API key and model name. The default model is `gpt-4o-mini`. For an OpenAI-compatible endpoint, choose that provider and enter its base URL.
7. Upload PDFs, fill in supplier metadata, and select **Evaluate suppliers**.

The SQLite database is initialized automatically at `data/rfp_evaluation.db` with the default criteria.

## Sample proposals

Four fictional 2–4-page proposal PDFs are in `sample_rfps/`:
- Apex Systems: stronger technical and security design, higher price.
- BrightPath Tech: lower price and faster timeline, less compliance detail.
- NexaWorks: balanced proposal with strong implementation and support.
- Orbit Digital: experience and references, less specific integration plan.

These are synthetic documents for testing only.

## Scoring and ranking

- Absolute weighted score (out of 10): `sum((score / max_score) * (weight / 100) * 10)`
- Criterion benchmark: highest score for that criterion in the current batch.
- Criterion gap: `supplier score - benchmark score`.
- Relative performance: `score / benchmark * 100`. If the benchmark is zero, relative performance is defined as 100% for all suppliers to avoid division by zero.
- Peer Performance Index (PPI): weighted average of criterion relative-performance percentages.
- Tie-breaks: PPI descending, submission date ascending, experience rating descending, supplier name ascending.

LLM outputs are validated and missing results are normalized to zero with warnings. The LLM does not calculate final arithmetic or choose rankings.

## Streamlit Community Cloud deployment

1. Create a GitHub repository and upload the project source. Do not commit API keys, `.env` files, or the local SQLite database.
2. Go to [Streamlit Community Cloud](https://share.streamlit.io/) and deploy the repository, selecting `app.py` as the main file.
3. Streamlit installs packages from `requirements.txt`.
4. In the deployed app, enter the API key in the sidebar at runtime. The key is held in Streamlit session state/widget handling and is not written to the database. Do not share your browser session with others.
5. SQLite files on hosted ephemeral storage may not be durable across app restarts/redeploys. For a persistent production deployment, migrate the database to a managed database.

## Security and privacy

- API keys are not hardcoded or stored in SQLite/exported JSON.
- Proposal text is sent to the configured LLM provider. Do not upload confidential supplier proposals unless your organization permits that data transfer.
- The app uses `temperature=0`, but LLM results can still vary by provider/model. Once scorecards are validated, arithmetic and tie-break ordering are deterministic.
- This is a decision-support prototype. Human review is recommended before procurement decisions.

## Known limitations

- Text-based PDFs are supported. Scanned/image-only PDFs are detected when extraction yields very little text; OCR is not included.
- The app assumes every supplier proposal in a run is scored against the same active criteria loaded for that run.
- Review warnings and evidence before relying on scores.
