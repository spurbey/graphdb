import ast
src = open("pipeline_api.py", encoding="utf-8").read()
print("Bug1 fixed (empty string filter):", "annotated = [s for s in states if s.get" in src)
print("Bug2 fixed (in_e traversal):", "in_e()" in src)
print("old is_not_null still present:", "is_not_null" in src)
ast.parse(src)
print("syntax OK")
