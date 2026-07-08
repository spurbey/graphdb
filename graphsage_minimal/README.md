# Minimal GraphSAGE Probe

This folder is a small experiment layer over the existing `sandbox/amo_nodes.json`
and `sandbox/amo_edges.json` artifacts.

It deliberately avoids PyTorch Geometric. The model is a plain PyTorch
GraphSAGE-style mean aggregator, so the same code runs locally on CPU and in
Colab on GPU.

## Local Flow

```powershell
python graphsage_minimal/prepare_graphsage_data.py
python graphsage_minimal/embed_eval_queries.py
python graphsage_minimal/train_graphsage.py --epochs 30 --device cpu
python graphsage_minimal/rank_with_graphsage.py
```

## Colab Flow

Upload `graphsage_minimal_bundle.zip` to Colab, unzip it, then run:

```bash
python graphsage_minimal/train_graphsage.py --epochs 50 --device cuda
python graphsage_minimal/rank_with_graphsage.py
```

The prepared data artifact contains active function embeddings, CALLS edges,
and name/file metadata. It does not include raw source code.

## Local Baseline Run

On the Windows laptop CPU, a 50-epoch run completed in under a minute and
reached held-out CALLS link prediction metrics around:

```text
test AUC: 0.813
test AP:  0.820
```

The retrieval ranking result is mixed. GraphSAGE is useful as a structural
reranker for weaker vector hits, but it should not replace vector search:

```text
memory ingestion hook: vector rank 1, GraphSAGE-heavy rank gets worse
memory context pack:   vector rank 24, GraphSAGE rerank improves to ~9
snapshot export:       vector rank 21, GraphSAGE rerank improves to ~4-11
```

Interpretation: for this graph, GraphSAGE has learned CALLS-neighborhood
structure, but broad structural weighting can bury exact lexical/semantic hits.
Use it only after vector candidate generation.
