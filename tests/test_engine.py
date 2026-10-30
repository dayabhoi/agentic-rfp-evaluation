import tempfile
import unittest
from pathlib import Path

from rfp_engine import default_criteria, normalize_evaluation, calculate_rankings, init_db, get_criteria

class EngineTests(unittest.TestCase):
    def test_missing_criteria_normalize_to_zero(self):
        criteria = default_criteria()
        normalized, warnings = normalize_evaluation({"criteria": []}, criteria)
        self.assertEqual(len(normalized["criteria"]), len(criteria))
        self.assertTrue(all(c["score"] == 0 for c in normalized["criteria"]))
        self.assertTrue(warnings)

    def test_ranking_is_deterministic_for_same_inputs(self):
        criteria = default_criteria()
        items = []
        for name, day, exp, score in [
            ("Beta", "2026-01-02", 4, 8),
            ("Alpha", "2026-01-01", 3, 8),
        ]:
            evaluation, warnings = normalize_evaluation({
                "supplier_name": name,
                "criteria": [{"criterion_id": c["criterion_id"], "score": score, "max_score": 10,
                              "justification": "test", "evidence": "test"} for c in criteria],
                "risks": [], "overall_summary": "test"
            }, criteria)
            items.append({"supplier_name": name, "submission_date": day, "experience_rating": exp,
                          "evaluation": evaluation, "warnings": warnings})
        ranked = calculate_rankings(items, criteria)
        self.assertEqual(ranked[0]["supplier_name"], "Alpha")
        self.assertEqual([x["final_rank"] for x in ranked], [1, 2])

    def test_database_seeds_criteria(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.db"
            init_db(db)
            self.assertEqual(len(get_criteria(db)), 5)

if __name__ == "__main__":
    unittest.main()
