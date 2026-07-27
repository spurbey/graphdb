import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pipeline_api

pipeline_api.initialize()
p = pipeline_api._pipeline
funcs = []
for v in p.G.vs:
    if "func_" in str(v.attributes().get("id", "")):
        funcs.append(v["id"])

test_funcs = funcs[:5]
print(f"Testing commit_review on {test_funcs}")
res = pipeline_api.commit_review(test_funcs)
import json
print(json.dumps(res, indent=2))
