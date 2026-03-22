# ZDPAS repo: practical `.saturn/rules.yaml` + `.saturn/risk.yaml`

Copy the `*.example` files into your **ZDPAS worktree** as:

```text
your-zdpas-repo/.saturn/rules.yaml
your-zdpas-repo/.saturn/risk.yaml
```

## When these files are loaded

Saturn loads them **only if** the directory **`.saturn/` exists** in the worktree root (`load_repo_config`).

| File | Applied when | Effect |
|------|----------------|--------|
| **`rules.yaml`** | `.saturn/` exists | Maps changed paths → **module names** and optional **test patterns** for `{modules}` / `{test_patterns}` in gate commands (see `gates/incremental.py`). |
| **`risk.yaml`** | `.saturn/` exists | **Before gates:** limits patch size and blocks edits to **restricted** paths/files; can **ignore** generated/noise paths in counts. |

## Critical: `.saturn/` and `gates.yaml`

If **`.saturn/` exists** but **`gates.yaml` is missing**, Saturn uses an **empty** gate list (no compile/tests). So if you add only `rules.yaml` / `risk.yaml`, you **must** also add a full **`gates.yaml`** (e.g. copy the three ZDPAS gates from Saturn `gates/config.py` plus any extras like Sonar/OWASP — see `examples/zdpas-security-gates/`).

If there is **no** `.saturn/` folder at all, Saturn **auto-discovers** ZDPAS gates and uses **default** risk limits from code (`_default_risk()`), not `risk.yaml`.

## `compile_strategy` in README

The top-level README mentions `incremental.compile_strategy`; the current loader **does not** read that field — only `module_mapping` and `test_mapping` are used. Treat it as documentation/future use, or omit it.

## Files in this folder

| File | Purpose |
|------|---------|
| **`rules.yaml.example`** | Safe default: rely on built-in ZDPAS module map; optional commented custom mappings. |
| **`risk.yaml.example`** | Typical limits + restricted infra paths + ignores for `.saturn` churn. |
