from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F

try:
    from sklearn.metrics import average_precision_score, roc_auc_score
except Exception:  # pragma: no cover
    average_precision_score = None
    roc_auc_score = None


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "graphsage_minimal" / "data"
OUT = ROOT / "graphsage_minimal" / "out"
OUT.mkdir(parents=True, exist_ok=True)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_data(device: torch.device):
    arr = np.load(DATA / "amo_calls_active.npz")
    x = torch.from_numpy(arr["x"]).to(device)
    train_edges = torch.from_numpy(arr["train_edges"]).long().to(device)
    val_edges = torch.from_numpy(arr["val_edges"]).long().to(device)
    test_edges = torch.from_numpy(arr["test_edges"]).long().to(device)

    # Message passing graph: use train edges only, mirrored as undirected.
    rev = train_edges.flip(0)
    mp_edges = torch.cat([train_edges, rev], dim=1)

    all_edges = torch.from_numpy(arr["edge_index"]).long()
    return x, mp_edges, train_edges, val_edges, test_edges, all_edges


def sample_negative_edges(num_nodes: int, positives: torch.Tensor, count: int, device: torch.device) -> torch.Tensor:
    existing = set()
    pos_cpu = positives.detach().cpu().numpy()
    for a, b in pos_cpu.T:
        if a == b:
            continue
        x, y = (int(a), int(b)) if a < b else (int(b), int(a))
        existing.add((x, y))

    negatives: set[tuple[int, int]] = set()
    while len(negatives) < count:
        a = random.randrange(num_nodes)
        b = random.randrange(num_nodes)
        if a == b:
            continue
        x, y = (a, b) if a < b else (b, a)
        if (x, y) in existing or (x, y) in negatives:
            continue
        negatives.add((x, y))
    return torch.tensor(sorted(negatives), dtype=torch.long, device=device).T


class SageLayer(nn.Module):
    def __init__(self, in_dim: int, out_dim: int):
        super().__init__()
        self.self_linear = nn.Linear(in_dim, out_dim)
        self.neigh_linear = nn.Linear(in_dim, out_dim)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        src, dst = edge_index
        agg = torch.zeros_like(x)
        agg.index_add_(0, dst, x[src])
        deg = torch.zeros(x.size(0), device=x.device, dtype=x.dtype)
        deg.index_add_(0, dst, torch.ones_like(dst, dtype=x.dtype))
        neigh = agg / deg.clamp_min(1.0).unsqueeze(1)
        return self.self_linear(x) + self.neigh_linear(neigh)


class GraphSAGE(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int, out_dim: int, dropout: float):
        super().__init__()
        self.layer1 = SageLayer(in_dim, hidden_dim)
        self.layer2 = SageLayer(hidden_dim, out_dim)
        self.dropout = dropout

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        h = self.layer1(x, edge_index)
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)
        h = self.layer2(h, edge_index)
        return F.normalize(h, p=2, dim=1)


def edge_logits(z: torch.Tensor, edges: torch.Tensor) -> torch.Tensor:
    return (z[edges[0]] * z[edges[1]]).sum(dim=1)


@torch.no_grad()
def evaluate(model, x, mp_edges, pos_edges, all_pos_edges, device):
    model.eval()
    z = model(x, mp_edges)
    neg_edges = sample_negative_edges(x.size(0), all_pos_edges, pos_edges.size(1), device)
    logits = torch.cat([edge_logits(z, pos_edges), edge_logits(z, neg_edges)]).detach().cpu().numpy()
    labels = np.concatenate([np.ones(pos_edges.size(1)), np.zeros(neg_edges.size(1))])
    metrics = {"loss": float(F.binary_cross_entropy_with_logits(
        torch.from_numpy(logits), torch.from_numpy(labels).float()
    ).item())}
    if roc_auc_score is not None:
        metrics["auc"] = float(roc_auc_score(labels, logits))
    if average_precision_score is not None:
        metrics["ap"] = float(average_precision_score(labels, logits))
    return metrics, z


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--hidden-dim", type=int, default=256)
    parser.add_argument("--out-dim", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()

    set_seed(args.seed)
    device = torch.device("cuda" if (args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available())) else "cpu")
    print(f"Device: {device}")

    x, mp_edges, train_edges, val_edges, test_edges, all_edges = load_data(device)
    print(f"Nodes: {x.size(0)}  features: {x.size(1)}  train edges: {train_edges.size(1)}")

    model = GraphSAGE(x.size(1), args.hidden_dim, args.out_dim, args.dropout).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val = -1.0
    best_state = None
    history = []
    all_edges_device = all_edges.to(device)

    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        z = model(x, mp_edges)
        neg_edges = sample_negative_edges(x.size(0), all_edges_device, train_edges.size(1), device)
        logits = torch.cat([edge_logits(z, train_edges), edge_logits(z, neg_edges)])
        labels = torch.cat([
            torch.ones(train_edges.size(1), device=device),
            torch.zeros(neg_edges.size(1), device=device),
        ])
        loss = F.binary_cross_entropy_with_logits(logits, labels)
        loss.backward()
        opt.step()

        if epoch == 1 or epoch % 5 == 0 or epoch == args.epochs:
            val_metrics, _ = evaluate(model, x, mp_edges, val_edges, all_edges_device, device)
            score = val_metrics.get("auc", -val_metrics["loss"])
            history.append({"epoch": epoch, "train_loss": float(loss.item()), "val": val_metrics})
            print(f"epoch {epoch:03d} train_loss={loss.item():.4f} val={val_metrics}")
            if score > best_val:
                best_val = score
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    test_metrics, z = evaluate(model, x, mp_edges, test_edges, all_edges_device, device)
    z_np = z.detach().cpu().numpy().astype(np.float32)
    np.save(OUT / "graphsage_embeddings.npy", z_np)
    torch.save(model.state_dict(), OUT / "graphsage_state.pt")

    metrics = {
        "device": str(device),
        "epochs": args.epochs,
        "nodes": int(x.size(0)),
        "train_edges": int(train_edges.size(1)),
        "val_edges": int(val_edges.size(1)),
        "test_edges": int(test_edges.size(1)),
        "test": test_metrics,
        "history": history,
    }
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"Test: {test_metrics}")
    print(f"Wrote {OUT / 'graphsage_embeddings.npy'}")
    print(f"Wrote {OUT / 'metrics.json'}")


if __name__ == "__main__":
    main()

