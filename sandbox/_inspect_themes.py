"""Quick inspection of theme_proportions on structural edges after regeneration."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pairs = json.loads((ROOT / "sandbox/amo_cochange_pairs.json").read_text(encoding="utf-8"))
edges = json.loads((ROOT / "sandbox/amo_edges.json").read_text(encoding="utf-8"))

structural_pairs = [p for p in pairs if p["category"] == "structural_redundant"]
structural_edges = [e for e in edges if e["type"] == "CO_CHANGE" and e["category"] == "structural_redundant"]

print(f"Total pairs:              {len(pairs)}")
print(f"Structural pairs:         {len(structural_pairs)}")
print(f"Structural CO_CHANGE edges: {len(structural_edges)}")
print()

# theme_proportions population
pairs_with = [p for p in structural_pairs if p.get("theme_proportions")]
pairs_without = [p for p in structural_pairs if not p.get("theme_proportions")]
edges_with = [e for e in structural_edges if e.get("theme_proportions")]

print(f"Structural pairs WITH theme_proportions:    {len(pairs_with)}/{len(structural_pairs)}")
print(f"Structural edges WITH theme_proportions:    {len(edges_with)}/{len(structural_edges)}")
print()

print("=== First 3 structural pairs WITH themes ===")
for p in pairs_with[:3]:
    src = p["source"].split("::")[-1]
    tgt = p["target"].split("::")[-1]
    print(f"  {src} <-> {tgt}")
    print(f"    theme_id:          {p['theme_id']}")
    print(f"    theme_proportions: {p['theme_proportions']}")
    print(f"    theme_labels:      {p.get('theme_labels', {})}")
    print()

print("=== First 3 structural pairs WITHOUT themes ===")
for p in pairs_without[:3]:
    src = p["source"].split("::")[-1]
    tgt = p["target"].split("::")[-1]
    print(f"  {src} <-> {tgt}")
    print(f"    source_commit_shas: {p['source_commit_shas']}")
    print(f"    explanation:        {p.get('explanation', {})}")
    print()

# All category breakdown
from collections import Counter
cat_counts = Counter(p["category"] for p in pairs)
print("=== Category breakdown ===")
for cat, count in sorted(cat_counts.items()):
    themed = sum(1 for p in pairs if p["category"] == cat and p.get("theme_proportions"))
    print(f"  {cat:<30} {count:>4} total  {themed:>4} with theme_proportions")
