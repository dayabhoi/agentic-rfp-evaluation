from pathlib import Path
from rfp_engine import init_db, get_criteria

db = Path(__file__).parent / "data" / "rfp_evaluation.db"
init_db(db)
print(f"Database initialized: {db}")
for criterion in get_criteria(db):
    print(criterion)
