"""Prompt A/B Testing v22 — compare prompt variants automatically.

Usage:
  ab = ABTestManager()
  ab.create("code_prompt", {"A": prompt_v1, "B": prompt_v2})
  variant, prompt = ab.get_variant("code_prompt", user_id="user123")
  # ... use prompt, collect result ...
  ab.record("code_prompt", variant, score=0.8)
  winner = ab.get_results("code_prompt")
"""
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Experiment:
    name: str
    variants: dict[str, str]  # variant_name → prompt_content
    results: dict[str, list[float]] = field(default_factory=lambda: {"A": [], "B": []})
    created_at: float = field(default_factory=time.time)


class ABTestManager:
    """Manage prompt A/B tests."""

    def __init__(self, storage_path: str = "data/ab_tests.json"):
        self.storage = Path(storage_path)
        self.experiments: dict[str, Experiment] = {}
        self._load()

    def create(self, name: str, variants: dict[str, str]):
        self.experiments[name] = Experiment(name=name, variants=variants)
        self._save()

    def get_variant(self, name: str, user_id: str = "") -> tuple[str, str]:
        """Get variant for user (consistent hashing)."""
        exp = self.experiments.get(name)
        if not exp:
            return "A", ""
        # Consistent hash → same user always gets same variant
        h = int(hashlib.md5(f"{name}:{user_id}".encode()).hexdigest(), 16)
        variant_names = sorted(exp.variants.keys())
        variant = variant_names[h % len(variant_names)]
        return variant, exp.variants[variant]

    def record(self, name: str, variant: str, score: float):
        exp = self.experiments.get(name)
        if exp and variant in exp.results:
            exp.results[variant].append(score)
            self._save()

    def get_results(self, name: str) -> dict:
        exp = self.experiments.get(name)
        if not exp:
            return {}
        results = {}
        for v, scores in exp.results.items():
            n = len(scores)
            avg = sum(scores) / n if n > 0 else 0
            results[v] = {"count": n, "avg_score": round(avg, 3)}
        # Determine winner
        avgs = {v: r["avg_score"] for v, r in results.items() if r["count"] > 0}
        winner = max(avgs, key=avgs.get) if avgs else None
        return {"variants": results, "winner": winner}

    def list_experiments(self) -> list[dict]:
        return [{"name": e.name, "variants": list(e.variants.keys()),
                 "total_samples": sum(len(v) for v in e.results.values())}
                for e in self.experiments.values()]

    def _save(self):
        self.storage.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for name, exp in self.experiments.items():
            data[name] = {"variants": exp.variants, "results": exp.results, "created_at": exp.created_at}
        self.storage.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _load(self):
        if self.storage.exists():
            try:
                data = json.loads(self.storage.read_text(encoding="utf-8"))
                for name, d in data.items():
                    self.experiments[name] = Experiment(
                        name=name, variants=d["variants"],
                        results=d.get("results", {"A": [], "B": []}),
                        created_at=d.get("created_at", 0))
            except Exception as _e:

                pass  # Silenced: see logs if needed