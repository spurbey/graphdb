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
import os
import re
import time
import git
import tree_sitter_python as tspython
from concurrent.futures import ThreadPoolExecutor, as_completed
from tree_sitter import Language, Parser
from helixdb import (
    Client, g, write_batch, read_batch,
    define_params, param, PropertyInput, PropertyValue, IndexSpec,
    Predicate, NodeRef,
)

REPO_PATH = "."
REPO_BRANCH = os.environ.get("REPO_BRANCH", "master")
REPO_NAME = os.environ.get("REPO_NAME", os.path.basename(os.path.abspath(REPO_PATH)))
MAX_COMMITS = int(os.environ.get("MAX_COMMITS", "0"))
HELIX_URL = "http://127.0.0.1:6969"

# ── Local embedding (sentence-transformers) ───────────────────────────────────
_EMBED_DIMS  = 384
_EMBED_MODEL = None

def _get_embedder():
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBED_MODEL

def _embed_batch(texts: list[str]) -> list[list[float]]:
    model = _get_embedder()
    if not texts:
        return []
    try:
        vecs = model.encode(texts, show_progress_bar=False, batch_size=256, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
    except Exception as e:
        print(f"  [embed error] {e}")
        results = []
        for i, t in enumerate(texts):
            try:
                v = model.encode([t], show_progress_bar=False, normalize_embeddings=True)[0]
                results.append(v.tolist())
            except Exception as e2:
                print(f"  [embed error] text[{i}] ({len(t)} chars): {e2}")
                results.append([0.0] * _EMBED_DIMS)
        return results

def _summarise(code: str) -> str:
    """Return a summary for a function — first docstring line, empty if none."""
    ds = re.search(r'"""(.+?)"""', code, re.DOTALL)
    return ds.group(1).strip().splitlines()[0] if ds else ""

PY_LANG = Language(tspython.language(), "python")
_parser = Parser()
_parser.set_language(PY_LANG)

K_COMMIT = "Commit"
K_FILE   = "FileIdentity"
K_FUNC   = "FunctionIdentity"
K_CLASS  = "ClassIdentity"
K_STATE  = "FunctionState"

ALL_NODE_KINDS = (K_COMMIT, K_FILE, K_FUNC, K_CLASS, K_STATE)

# tracks func_id -> latest state_id so we can flip status at end
_latest_state: dict[str, str] = {}

_EDGE_PARAMS = define_params({"src_id": param.string(), "tgt_id": param.string()})

_SKIP_FILES = {"scalable_ingest.py", "semantic_pass.py", "dump_viz.py",
               "level_1_parser.py", "ingestion_process.txt",
               "graph_mcp_server.py", "graph_tools.py",
               "_test_loop.py", "_test_queries.py", "_test_vector.py",
               "_test_3hop.py", "_test_bench.py"}

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
    # Vector indexing is now handled by turbovec (TurboQuant) instead of HelixDB HNSW
    c.query().dynamic(batch.returning(names).to_dynamic_request()).send()


def _build_node_props(kind: str, node_id: str, props: dict) -> dict:
    """Convert props dict into HelixDB PropertyInput format."""
    all_props = {"node_id": PropertyInput.value(node_id)}
    for k, v in props.items():
        if isinstance(v, list) and v and isinstance(v[0], float):
            all_props[k] = PropertyInput.value(PropertyValue.f32_array(v))
        else:
            all_props[k] = PropertyInput.value(str(v)[:4000])
    return all_props


class BatchWriter:
    """Accumulate nodes/edges and flush to HelixDB in large batches."""

    def __init__(self, c, batch_size=100):
        self.c = c
        self.batch_size = batch_size
        self._nodes = []
        self._node_seen = set()
        self._edges = []
        self._turbovec_ops = []

    def add_node(self, kind: str, node_id: str, props: dict):
        if node_id in self._node_seen:
            return
        self._node_seen.add(node_id)
        if "code_vec" in props and props["code_vec"]:
            import turbovec_adapter
            props["code_vector_id"] = str(turbovec_adapter.stable_vector_id(node_id, "code"))
        if kind == "FunctionState" and props.get("memory_vec"):
            import turbovec_adapter
            props["memory_vector_id"] = str(turbovec_adapter.stable_vector_id(node_id, "memory"))
        self._nodes.append((kind, node_id, props.copy()))
        if "code_vec" in props and props["code_vec"]:
            self._turbovec_ops.append(("code", node_id, props["code_vec"]))
        if kind == "FunctionState":
            if "memory_vec" in props and props["memory_vec"]:
                self._turbovec_ops.append(("memory", node_id, props["memory_vec"]))
        if len(self._nodes) >= self.batch_size:
            self.flush_nodes()

    def add_edge(self, from_id: str, to_id: str, label: str, src_kind: str, tgt_kind: str):
        self._edges.append((from_id, to_id, label, src_kind, tgt_kind))
        if len(self._edges) >= self.batch_size:
            self.flush_edges()

    def flush_nodes(self):
        if not self._nodes:
            return
        batch = write_batch()
        names = []
        for i, (kind, node_id, props) in enumerate(self._nodes):
            hp = _build_node_props(kind, node_id, props)
            name = f"n{i}"
            batch = batch.var_as(name, g().add_n(kind, hp))
            names.append(name)
        try:
            self.c.query().dynamic(batch.returning(names).to_dynamic_request()).send()
        except Exception as e:
            print(f"    [node batch error] {e}")
        self._nodes = []

    def flush_edges(self):
        if not self._edges:
            return
        batch = write_batch()
        edge_names = []
        all_params = {}
        for i, (from_id, to_id, label, src_kind, tgt_kind) in enumerate(self._edges):
            p_src = f"src_{i}"
            p_tgt = f"tgt_{i}"
            v_src = f"vsrc_{i}"
            v_tgt = f"vtgt_{i}"
            v_e   = f"ve_{i}"
            batch = (
                batch
                .var_as(v_src, g().n_with_label(src_kind).where(Predicate.eq_param("node_id", p_src)))
                .var_as(v_tgt, g().n_with_label(tgt_kind).where(Predicate.eq_param("node_id", p_tgt)))
                .var_as(v_e,   g().n(NodeRef.var(v_src)).add_e(label, NodeRef.var(v_tgt), {}))
            )
            edge_names.append(v_e)
            all_params[p_src] = from_id
            all_params[p_tgt] = to_id
        edge_params_def = define_params({k: param.string() for k in all_params})
        try:
            self.c.query().dynamic(
                batch.returning(edge_names).to_dynamic_request(edge_params_def, all_params)
            ).send()
        except Exception:
            pass
        self._edges = []

    def flush(self):
        self.flush_nodes()
        self.flush_edges()
        self._flush_turbovec()

    def _flush_turbovec(self):
        if not self._turbovec_ops:
            return
        import turbovec_adapter
        for idx, doc_id, vec in self._turbovec_ops:
            try:
                if idx == "code":
                    turbovec_adapter.code_index.insert(doc_id, vec)
                elif idx == "memory":
                    turbovec_adapter.memory_index.insert(doc_id, vec)
            except Exception:
                pass
        turbovec_adapter.code_index.save()
        turbovec_adapter.memory_index.save()
        self._turbovec_ops = []


# ── tree-sitter extraction ────────────────────────────────────────────────────

def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _code_hash(code: str) -> str:
    return hashlib.sha1(code.encode()).hexdigest()[:12]

def _nid(template: str, *args) -> str:
    """Create a node ID prefixed with repo name to avoid cross-repo collisions."""
    return f"{REPO_NAME}:{template.format(*args)}"


def _parse_file(file_path: str, source: str, commit_hash: str,
                prev_state_tracker: dict, prev_func_registry: dict,
                prev_edge_seen: set):
    """
    Parse one file at one commit. Does NOT mutate the passed-in dicts/sets.
    Returns (nodes, edges, local_state_updates, local_registry_updates, local_edge_keys).
    local_state_updates: dict func_id -> state_id for functions defined in this file.
    local_edge_keys: set of (from, label, to) for edges created in this file.
    """
    src  = source.encode("utf-8")
    tree = _parser.parse(src)
    root = tree.root_node
    safe = file_path.replace("/", "_").replace("\\", "_").replace(".py", "")

    nodes, edges = [], []
    file_id = _nid("file_{}", safe)

    nodes.append({"kind": K_FILE, "node_id": file_id, "props": {"file": file_path}})

    # Local mutable state for this file
    local_state_tracker = {}
    local_func_registry = {}
    local_edge_seen = set()

    def _eff_func_registry():
        r = dict(prev_func_registry)
        r.update(local_func_registry)
        return r

    def _edge(frm, frm_kind, lbl, to, to_kind):
        key = (frm, lbl, to)
        if key not in prev_edge_seen and key not in local_edge_seen:
            local_edge_seen.add(key)
            edges.append({"from": frm, "from_kind": frm_kind,
                          "label": lbl, "to": to, "to_kind": to_kind})

    _edge(_nid("commit_{}", commit_hash), K_COMMIT, "CONTAINS", file_id, K_FILE)

    # ── imports ──────────────────────────────────────────────────────────────
    for node in root.children:
        if node.type == "import_statement":
            for name_node in node.children_by_field_name("name"):
                mod    = _text(name_node, src).split(".")[0]
                mod_id = _nid("file_{}", mod)
                nodes.append({"kind": K_FILE, "node_id": mod_id,
                               "props": {"file": mod, "external": "true"}})
                _edge(file_id, K_FILE, "IMPORTS", mod_id, K_FILE)
        elif node.type == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            if mod_node:
                mod    = _text(mod_node, src).split(".")[0]
                mod_id = _nid("file_{}", mod)
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
            cls_id   = _nid("class_{}_{}", safe, cls_name)
            cls_code = _text(node, src)
            nodes.append({"kind": K_CLASS, "node_id": cls_id,
                          "props": {"name": cls_name, "file": file_path,
                                    "_embed_text": cls_code[:2000]}})
            _edge(scope_id, scope_kind, "CONTAINS", cls_id, K_CLASS)

            bases = node.child_by_field_name("superclasses")
            if bases:
                for base in bases.children:
                    if base.type == "identifier":
                        base_name = _text(base, src)
                        base_id   = _nid("class_{}_{}", safe, base_name)
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
            func_id   = _nid("func_{}_{}", safe, func_name)
            state_id  = _nid("state_{}_{}_{}", safe, func_name, commit_hash)
            code      = _text(fn_node, src)

            nodes.append({"kind": K_FUNC, "node_id": func_id,
                          "props": {"name": func_name, "file": file_path,
                                    "_embed_text": code[:2000]}})
            _summary = _summarise(code)
            is_new = func_id not in prev_state_tracker and func_id not in local_state_tracker
            nodes.append({"kind": K_STATE, "node_id": state_id,
                          "props": {
                              "code":            code[:4000],
                              "code_hash":       _code_hash(code),
                              "commit":          commit_hash,
                              "function_id":     func_id,
                              "ai_summary":      _summary,
                              "memory":          "",
                              "memory_vec":      [0.0] * _EMBED_DIMS,
                              "status":          "active",
                          }})

            local_func_registry[func_name] = func_id

            _edge(scope_id,              scope_kind, "CONTAINS",        func_id,  K_FUNC)
            _edge(func_id,               K_FUNC,     "HAS_STATE",       state_id, K_STATE)
            _edge(_nid("commit_{}", commit_hash), K_COMMIT, "GENERATED",       state_id, K_STATE)
            if is_new:
                _edge(_nid("commit_{}", commit_hash), K_COMMIT, "INTRODUCED",  state_id, K_STATE)

            # PREVIOUS_VERSION: check both prev and local state_tracker
            if func_id in prev_state_tracker:
                _edge(state_id, K_STATE, "PREVIOUS_VERSION",
                      prev_state_tracker[func_id], K_STATE)
            elif func_id in local_state_tracker:
                _edge(state_id, K_STATE, "PREVIOUS_VERSION",
                      local_state_tracker[func_id], K_STATE)
            local_state_tracker[func_id] = state_id

            def collect_calls(n):
                if n.type == "call":
                    fn_field = n.child_by_field_name("function")
                    if fn_field:
                        callee    = _text(fn_field, src).split("(")[0].split(".")[-1]
                        resolved = local_func_registry.get(callee) or prev_func_registry.get(callee)
                        if resolved:
                            _edge(func_id, K_FUNC, "CALLS", resolved, K_FUNC)
                for child in n.children:
                    collect_calls(child)

            body = fn_node.child_by_field_name("body")
            if body:
                collect_calls(body)
        else:
            for child in node.children:
                walk(child, scope_id=scope_id, scope_kind=scope_kind)

    walk(root)
    return nodes, edges, local_state_tracker, local_func_registry, local_edge_seen

# ── main ingestion loop ───────────────────────────────────────────────────────

def _resolve_embeddings(nodes: list[dict]):
    embed_list = []
    for n in nodes:
        p = n["props"]
        if "_embed_text" in p:
            embed_list.append((n, "code_vec", p.pop("_embed_text")))
    if not embed_list:
        return
    texts = [t for _, _, t in embed_list]
    print(f"  Embedding {len(texts)} texts...")
    vecs = _embed_batch(texts)
    for (n, field, _), vec in zip(embed_list, vecs):
        n["props"][field] = vec


def run_ingestion():
    repo = git.Repo(REPO_PATH)
    c    = _helix()

    print("Ensuring HelixDB indexes...")
    _ensure_indexes(c)

    commits = list(repo.iter_commits(REPO_BRANCH))
    commits.reverse()
    if MAX_COMMITS > 0:
        commits = commits[:MAX_COMMITS]

    master_nodes, master_edges = [], []
    state_tracker  = {}
    func_registry  = {}
    edge_seen      = set()   # global dedup for CONTAINS + structural edges
    prev_commit_id = None

    print(f"Starting ingestion ({len(commits)} commits)...\n")
    t_start = time.time()

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

        commit_id   = _nid("commit_{}", h)
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

        if prev_commit_id:
            key = (prev_commit_id, "NEXT_COMMIT", commit_id)
            if key not in edge_seen:
                edge_seen.add(key)
                master_edges.append({"from": prev_commit_id, "from_kind": K_COMMIT,
                                     "label": "NEXT_COMMIT", "to": commit_id, "to_kind": K_COMMIT})
        prev_commit_id = commit_id

        # Snapshot global state for this commit's parallel parse
        state_snapshot = dict(state_tracker)
        reg_snapshot   = dict(func_registry)
        edge_snapshot  = set(edge_seen)
        file_count     = len(py_files)

        if file_count == 0:
            continue

        with ThreadPoolExecutor(max_workers=min(8, file_count)) as executor:
            futures = {}
            for file_path in py_files:
                try:
                    blob   = commit.tree / file_path
                    source = blob.data_stream.read().decode("utf-8")
                    fut = executor.submit(
                        _parse_file, file_path, source, h,
                        state_snapshot, reg_snapshot, edge_snapshot
                    )
                    futures[fut] = file_path
                except (KeyError, SyntaxError) as ex:
                    print(f"  -> skip ({ex})")

            for future in as_completed(futures):
                file_path = futures[future]
                try:
                    n_nodes, n_edges, loc_tracker, loc_registry, loc_edges = future.result()
                    master_nodes.extend(n_nodes)
                    for e in n_edges:
                        key = (e["from"], e["label"], e["to"])
                        if key not in edge_seen:
                            edge_seen.add(key)
                            master_edges.append(e)
                    state_tracker.update(loc_tracker)
                    _latest_state.update(loc_tracker)
                    func_registry.update(loc_registry)
                except Exception as ex:
                    print(f"  -> skip ({ex})")

    t_parse = time.time()
    print(f"\nParsed {len(master_nodes)} nodes, {len(master_edges)} edges in {t_parse - t_start:.1f}s.")

    # ── Resolve embeddings in batch ──────────────────────────────────────────
    _resolve_embeddings(master_nodes)
    print("  Embeddings done.")

    # ── Mark superseded states ────────────────────────────────────────────────
    head_ids = set(_latest_state.values())
    func_state_count = 0
    for n in master_nodes:
        if n["kind"] != K_STATE:
            continue
        func_state_count += 1
        sid = n["node_id"]
        n["props"]["status"] = "active" if sid in head_ids else "superseded"

    print(f"  Status: {len(head_ids)} active, {func_state_count - len(head_ids)} superseded.")

    # ── Batch-write to HelixDB ──────────────────────────────────────────────
    print("Writing to HelixDB...")
    bw = BatchWriter(c)
    for n in master_nodes:
        bw.add_node(n["kind"], n["node_id"], n["props"])
    bw.flush_nodes()
    for e in master_edges:
        bw.add_edge(e["from"], e["to"], e["label"], e["from_kind"], e["to_kind"])
    bw.flush_edges()
    bw._flush_turbovec()

    t_write = time.time()
    print(f"  HelixDB write done in {t_write - t_parse:.1f}s.")

    # ── JSON artefacts ────────────────────────────────────────────────────────
    for n in master_nodes:
        n["props"].pop("_embed_text", None)
        for k in list(n["props"].keys()):
            if isinstance(n["props"][k], list) and k.endswith("_vec"):
                n["props"][k] = f"[{len(n['props'][k])} floats]"

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
            "summary":  p.get("ai_summary") or p.get("ai_rationale") or str(p.get("code", ""))[:200],
            "status":   p.get("status", "active"),
            "metadata": p,
        })
    viz_edges = [{"source": e["from"], "target": e["to"], "kind": e["label"]}
                 for e in master_edges]
    with open("graph_viz.json", "w") as f:
        json.dump({"nodes": viz_nodes, "edges": viz_edges}, f, indent=2)

    print(f"Written graph_payload.json + graph_viz.json ({time.time() - t_start:.1f}s total).")


if __name__ == "__main__":
    run_ingestion()
