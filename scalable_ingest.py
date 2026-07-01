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
import re
import urllib.request
import git
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from helixdb import (
    Client, g, write_batch, read_batch,
    define_params, param, PropertyInput, PropertyValue, IndexSpec,
    Predicate, NodeRef,
)

REPO_PATH = "."
HELIX_URL = "http://127.0.0.1:6969"

# ── OpenRouter embedding ──────────────────────────────────────────────────────
_EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
_EMBED_DIMS  = 2048

def _load_api_key() -> str:
    try:
        for line in open(".env"):
            if "=" in line:
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""

_API_KEY = _load_api_key()

def _embed(text: str) -> list[float]:
    """Return a 2048-dim embedding vector from OpenRouter. Returns zeros on failure."""
    if not _API_KEY or not text.strip():
        return [0.0] * _EMBED_DIMS
    try:
        payload = json.dumps({"model": _EMBED_MODEL, "input": text[:2000]}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=payload,
            headers={"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return resp["data"][0]["embedding"]
    except Exception as e:
        print(f"  [embed warn] {e}")
        return [0.0] * _EMBED_DIMS

# Inline summaries — same source of truth as semantic_pass._SUMMARIES.
# Populated at insert time so the text index picks them up immediately.
_SUMMARIES = {
    "create_user":       "Stores a username-password pair in the in-memory users dict; validates non-empty inputs and enforces minimum password length.",
    "get_user":          "Looks up and returns the stored password for a username from the in-memory dict, or None if absent.",
    "delete_user":       "Removes a user entry from the in-memory dict and returns True, or False if the username was not present.",
    "list_users":        "Returns a list of all currently registered usernames from the in-memory store.",
    "user_exists":       "Returns True if the given username exists in the in-memory store, False otherwise.",
    "signup":            "Registers a new user after validating username format and enforcing minimum password length; returns a structured JSON response.",
    "login":             "Authenticates a user by verifying their password against the stored credential; returns a structured JSON success or failure response.",
    "validate_username": "Validates that a username is non-empty, at least 3 characters, and alphanumeric; raises ValidationError otherwise.",
    "logout":            "Ends a user session by confirming the user exists; returns a structured response. Does not verify password.",
    "delete_account":    "Permanently removes a user account after verifying credentials; calls login internally then drops the record from the store.",
}

def _summarise(code: str) -> str:
    """Return a summary for a function — lookup by name, fallback to first docstring line."""
    m = re.match(r'\s*(?:async\s+)?def\s+(\w+)', code)
    if m and m.group(1) in _SUMMARIES:
        return _SUMMARIES[m.group(1)]
    # fallback: first docstring line
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

_SKIP_FILES = {"scalable_ingest.py", "semantic_pass.py", "dump_viz.py",
               "level_1_parser.py", "ingestion_process.txt",
               "graph_mcp_server.py", "graph_tools.py",
               "_test_loop.py", "_test_queries.py", "_test_vector.py",
               "_test_3hop.py", "_test_bench.py"}

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
    # vector index on FunctionState.ai_summary_vec for semantic search
    batch = batch.var_as("vec_idx", g().create_index_if_not_exists(
        IndexSpec.node_vector(K_STATE, "ai_summary_vec")
    ))
    names.append("vec_idx")
    c.query().dynamic(batch.returning(names).to_dynamic_request()).send()


def _upsert_node(c, kind: str, node_id: str, props: dict):
    """Insert node; silently skips if node_id already exists."""
    all_props = {"node_id": PropertyInput.value(node_id)}
    for k, v in props.items():
        if isinstance(v, list) and v and isinstance(v[0], float):
            all_props[k] = PropertyInput.value(PropertyValue.f32_array(v))
        else:
            all_props[k] = PropertyInput.value(str(v)[:4000])
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
            _summary = _summarise(code)
            nodes.append({"kind": K_STATE, "node_id": state_id,
                          "props": {
                              "code":            code[:4000],
                              "code_hash":       _code_hash(code),
                              "commit":          commit_hash,
                              "function_id":     func_id,
                              "ai_summary":      _summary,
                              "ai_summary_vec":  _embed(_summary),
                              "status":          "active",
                          }})

            func_registry[func_name] = func_id

            _edge(scope_id,              scope_kind, "CONTAINS",        func_id,  K_FUNC)
            _edge(func_id,               K_FUNC,     "HAS_STATE",       state_id, K_STATE)
            _edge(f"commit_{commit_hash}", K_COMMIT, "GENERATED",       state_id, K_STATE)

            if func_id in state_tracker:
                _edge(state_id, K_STATE, "PREVIOUS_VERSION",
                      state_tracker[func_id], K_STATE)
            state_tracker[func_id] = state_id
            _latest_state[func_id] = state_id  # track HEAD state per function

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

    # ── Mark superseded states ────────────────────────────────────────────────
    # All FunctionState nodes are written with status="active". Now demote any
    # state that is NOT the HEAD (latest) for its function to "superseded".
    head_ids = set(_latest_state.values())
    _PARAMS_STATUS = define_params({"nid": param.string(), "val": param.string()})
    for n in master_nodes:
        if n["kind"] != K_STATE:
            continue
        sid = n["node_id"]
        status = "active" if sid in head_ids else "superseded"
        n["props"]["status"] = status
        # patch in HelixDB: set_property is the lightweight way
        try:
            c.query().dynamic(
                write_batch()
                .var_as("n", g().n_with_label(K_STATE)
                         .where(Predicate.eq_param("node_id", "nid"))
                         .set_property("status", PropertyInput.param("val")))
                .returning(["n"])
                .to_dynamic_request(_PARAMS_STATUS, {"nid": sid, "val": status})
            ).send()
        except Exception:
            pass

    print(f"Status patched: {len(head_ids)} active, "
          f"{sum(1 for n in master_nodes if n['kind']==K_STATE) - len(head_ids)} superseded.")

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
