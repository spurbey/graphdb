"""
Repo graph ingestion engine — tree-sitter edition.
Extracts: CONTAINS, IMPORTS, INHERITS, CALLS, HAS_STATE, GENERATED, PREVIOUS_VERSION, NEXT_COMMIT edges.
Writes to HelixDB (localhost:6969) + JSON viz files.

Schema (flattened):
  Commit   --CONTAINS-->       FileIdentity
  Commit   --GENERATED-->      FunctionState
  FileIdentity --CONTAINS-->   FunctionIdentity | ClassIdentity
  ClassIdentity --CONTAINS-->  FunctionIdentity
  ClassIdentity --INHERITS-->  ClassIdentity
  FunctionIdentity --HAS_STATE--> FunctionState
  FunctionIdentity --CALLS-->  FunctionIdentity
  FunctionState --PREVIOUS_VERSION--> FunctionState
  Commit   --NEXT_COMMIT-->    Commit
"""

import hashlib
import json
import git
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from helixdb import (
    Client, g, write_batch, read_batch,
    define_params, param, PropertyInput, IndexSpec,
    Predicate, NodeRef,
)

REPO_PATH = "."
HELIX_URL = "http://127.0.0.1:6969"

PY_LANG = Language(tspython.language(), "python")
_parser = Parser()
_parser.set_language(PY_LANG)

K_COMMIT = "Commit"
K_FILE   = "FileIdentity"
K_FUNC   = "FunctionIdentity"
K_CLASS  = "ClassIdentity"
K_STATE  = "FunctionState"

ALL_NODE_KINDS = (K_COMMIT, K_FILE, K_FUNC, K_CLASS, K_STATE)

_SKIP_FILES = {"scalable_ingest.py", "semantic_pass.py", "dump_viz.py",
               "level_1_parser.py", "ingestion_process.txt"}

_EDGE_PARAMS = define_params({"src_id": param.string(), "tgt_id": param.string()})


# ── HelixDB helpers ───────────────────────────────────────────────────────────

def _helix():
    return Client(HELIX_URL)


def _ensure_indexes(c):
    batch = write_batch()
    names = []
    for kind in ALL_NODE_KINDS:
        name = f"idx_{kind}"
        batch = batch.var_as(name, g().create_index_if_not_exists(
            IndexSpec.node_unique_equality(kind, "node_id")
        ))
        names.append(name)
    c.query().dynamic(batch.returning(names).to_dynamic_request()).send()


def _upsert_node(c, kind: str, node_id: str, props: dict):
    """Insert node; silently skips if node_id already exists."""
    all_props = {
        "node_id": PropertyInput.value(node_id),
        **{k: PropertyInput.value(str(v)[:4000]) for k, v in props.items()},
    }
    try:
        c.query().dynamic(
            write_batch().var_as("n", g().add_n(kind, all_props)).returning(["n"]).to_dynamic_request()
        ).send()
    except Exception:
        pass


def _insert_edge(c, from_id: str, to_id: str, label: str, src_kind: str, tgt_kind: str):
    """Insert a directed edge; silently skips duplicates or missing endpoints."""
    batch = (
        write_batch()
        .var_as("src", g().n_with_label(src_kind).where(Predicate.eq_param("node_id", "src_id")))
        .var_as("tgt", g().n_with_label(tgt_kind).where(Predicate.eq_param("node_id", "tgt_id")))
        .var_as("e",   g().n(NodeRef.var("src")).add_e(label, NodeRef.var("tgt"), {}))
        .returning(["e"])
    )
    try:
        c.query().dynamic(
            batch.to_dynamic_request(_EDGE_PARAMS, {"src_id": from_id, "tgt_id": to_id})
        ).send()
    except Exception:
        pass


# ── tree-sitter extraction ────────────────────────────────────────────────────

def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _code_hash(code: str) -> str:
    return hashlib.sha1(code.encode()).hexdigest()[:12]


def extract_graph(file_path: str, source: str, commit_hash: str,
                  state_tracker: dict, func_registry: dict,
                  edge_seen: set):
    """
    Return (nodes, edges) for one file at one commit.
    edge_seen: global set of (from_id, label, to_id) to deduplicate CONTAINS edges.
    """
    src  = source.encode("utf-8")
    tree = _parser.parse(src)
    root = tree.root_node
    safe = file_path.replace("/", "_").replace("\\", "_").replace(".py", "")

    nodes, edges = [], []
    file_id = f"file_{safe}"

    nodes.append({"kind": K_FILE, "node_id": file_id, "props": {"file": file_path}})

    def _edge(frm, frm_kind, lbl, to, to_kind):
        key = (frm, lbl, to)
        if key not in edge_seen:
            edge_seen.add(key)
            edges.append({"from": frm, "from_kind": frm_kind,
                          "label": lbl, "to": to, "to_kind": to_kind})

    _edge(f"commit_{commit_hash}", K_COMMIT, "CONTAINS", file_id, K_FILE)

    # ── imports ──────────────────────────────────────────────────────────────
    for node in root.children:
        if node.type == "import_statement":
            for name_node in node.children_by_field_name("name"):
                mod    = _text(name_node, src).split(".")[0]
                mod_id = f"file_{mod}"
                nodes.append({"kind": K_FILE, "node_id": mod_id,
                               "props": {"file": mod, "external": "true"}})
                _edge(file_id, K_FILE, "IMPORTS", mod_id, K_FILE)
        elif node.type == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            if mod_node:
                mod    = _text(mod_node, src).split(".")[0]
                mod_id = f"file_{mod}"
                nodes.append({"kind": K_FILE, "node_id": mod_id,
                               "props": {"file": mod, "external": "true"}})
                _edge(file_id, K_FILE, "IMPORTS", mod_id, K_FILE)

    # ── classes + functions ───────────────────────────────────────────────────
    def walk(node, scope_id=file_id, scope_kind=K_FILE):
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            cls_name = _text(name_node, src)
            cls_id   = f"class_{safe}_{cls_name}"
            nodes.append({"kind": K_CLASS, "node_id": cls_id,
                          "props": {"name": cls_name, "file": file_path}})
            _edge(scope_id, scope_kind, "CONTAINS", cls_id, K_CLASS)

            bases = node.child_by_field_name("superclasses")
            if bases:
                for base in bases.children:
                    if base.type == "identifier":
                        base_name = _text(base, src)
                        base_id   = f"class_{safe}_{base_name}"
                        _edge(cls_id, K_CLASS, "INHERITS", base_id, K_CLASS)

            for child in node.children:
                walk(child, scope_id=cls_id, scope_kind=K_CLASS)

        elif node.type in ("function_definition", "decorated_definition"):
            fn_node = node if node.type == "function_definition" \
                           else node.child_by_field_name("definition")
            if fn_node is None:
                return
            name_node = fn_node.child_by_field_name("name")
            if not name_node:
                return
            func_name = _text(name_node, src)
            func_id   = f"func_{safe}_{func_name}"
            state_id  = f"state_{safe}_{func_name}_{commit_hash}"
            code      = _text(fn_node, src)

            nodes.append({"kind": K_FUNC, "node_id": func_id,
                          "props": {"name": func_name, "file": file_path}})
            nodes.append({"kind": K_STATE, "node_id": state_id,
                          "props": {
                              "code":        code[:4000],
                              "code_hash":   _code_hash(code),
                              "commit":      commit_hash,
                              "function_id": func_id,
                              "ai_summary":  "",   # filled by semantic_pass.py
                          }})

            func_registry[func_name] = func_id

            _edge(scope_id,              scope_kind, "CONTAINS",        func_id,  K_FUNC)
            _edge(func_id,               K_FUNC,     "HAS_STATE",       state_id, K_STATE)
            _edge(f"commit_{commit_hash}", K_COMMIT, "GENERATED",       state_id, K_STATE)

            if func_id in state_tracker:
                _edge(state_id, K_STATE, "PREVIOUS_VERSION",
                      state_tracker[func_id], K_STATE)
            state_tracker[func_id] = state_id

            def collect_calls(n):
                if n.type == "call":
                    fn_field = n.child_by_field_name("function")
                    if fn_field:
                        callee    = _text(fn_field, src).split("(")[0].split(".")[-1]
                        if callee in func_registry:
                            _edge(func_id, K_FUNC, "CALLS",
                                  func_registry[callee], K_FUNC)
                for child in n.children:
                    collect_calls(child)

            body = fn_node.child_by_field_name("body")
            if body:
                collect_calls(body)
        else:
            for child in node.children:
                walk(child, scope_id=scope_id, scope_kind=scope_kind)

    walk(root)
    return nodes, edges


# ── main ingestion loop ───────────────────────────────────────────────────────

def run_ingestion():
    repo = git.Repo(REPO_PATH)
    c    = _helix()

    print("Ensuring HelixDB indexes...")
    _ensure_indexes(c)

    commits = list(repo.iter_commits("master"))
    commits.reverse()

    master_nodes, master_edges = [], []
    state_tracker  = {}
    func_registry  = {}
    edge_seen      = set()   # global dedup for CONTAINS + structural edges
    prev_commit_id = None

    print("Starting ingestion...\n")

    for commit in commits:
        h      = commit.hexsha[:7]
        author = str(commit.author)
        print(f"Commit [{h}] {commit.message.strip()[:60]}")

        changed_files = (
            list(commit.stats.files.keys()) if not commit.parents
            else [d.b_path for d in commit.parents[0].diff(commit) if d.b_path]
        )
        py_files = [f for f in changed_files
                    if f.endswith(".py") and f.split("/")[-1] not in _SKIP_FILES]

        commit_id   = f"commit_{h}"
        commit_node = {
            "kind":    K_COMMIT,
            "node_id": commit_id,
            "props": {
                "hash":        commit.hexsha,
                "author":      author,
                "msg":         commit.message.strip(),
                "timestamp":   commit.committed_datetime.isoformat(),
                "ai_rationale": "",   # filled by semantic_pass.py
            },
        }
        master_nodes.append(commit_node)
        _upsert_node(c, K_COMMIT, commit_id, commit_node["props"])

        if prev_commit_id:
            key = (prev_commit_id, "NEXT_COMMIT", commit_id)
            if key not in edge_seen:
                edge_seen.add(key)
                master_edges.append({"from": prev_commit_id, "from_kind": K_COMMIT,
                                     "label": "NEXT_COMMIT", "to": commit_id, "to_kind": K_COMMIT})
                _insert_edge(c, prev_commit_id, commit_id, "NEXT_COMMIT", K_COMMIT, K_COMMIT)
        prev_commit_id = commit_id

        for file_path in py_files:
            print(f"  -> {file_path}")
            try:
                blob   = commit.tree / file_path
                source = blob.data_stream.read().decode("utf-8")
                nodes, edges = extract_graph(
                    file_path, source, h,
                    state_tracker, func_registry, edge_seen
                )
                master_nodes.extend(nodes)
                master_edges.extend(edges)

                for n in nodes:
                    _upsert_node(c, n["kind"], n["node_id"], n["props"])
                for e in edges:
                    _insert_edge(c, e["from"], e["to"], e["label"],
                                 e["from_kind"], e["to_kind"])

            except (KeyError, SyntaxError) as ex:
                print(f"  -> skip ({ex})")

    print(f"\nDone. {len(master_nodes)} nodes, {len(master_edges)} edges.")

    # ── JSON artefacts ────────────────────────────────────────────────────────
    with open("graph_payload.json", "w") as f:
        json.dump(
            {"nodes": [{"type": n["kind"], "id": n["node_id"], **n["props"]}
                       for n in master_nodes],
             "edges": [{"from": e["from"], "label": e["label"], "to": e["to"]}
                       for e in master_edges]},
            f, indent=2
        )

    viz_nodes = []
    for n in master_nodes:
        p     = n["props"]
        label = p.get("name") or p.get("msg", "").split("\n")[0] or p.get("file") or n["node_id"]
        viz_nodes.append({
            "id":       n["node_id"],
            "kind":     n["kind"],
            "label":    label[:60],
            "summary":  p.get("ai_summary") or p.get("ai_rationale") or p.get("code", "")[:200],
            "status":   "active",
            "metadata": p,
        })
    viz_edges = [{"source": e["from"], "target": e["to"], "kind": e["label"]}
                 for e in master_edges]
    with open("graph_viz.json", "w") as f:
        json.dump({"nodes": viz_nodes, "edges": viz_edges}, f, indent=2)

    print("Written graph_payload.json + graph_viz.json")


if __name__ == "__main__":
    run_ingestion()
