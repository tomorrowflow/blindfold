"""blindfold_devtools — source-run-only Diagnostic session tooling (ADR-0047).

A sibling top-level package, not ``blindfold.devtools``: ``packaging/blindfold-proxy.spec``'s
``datas=collect_data_files("blindfold")`` sweeps every non-.py file under the ``blindfold``
package directory, so a nested devtools fixture would ride into the release binary even with
the module itself excluded. Living outside ``blindfold.*`` keeps this package untouched by
that sweep and unreachable from ``packaging/blindfold_proxy_entry.py``'s import graph, so
PyInstaller never bundles it (ADR-0047 §2/§12).

No module under ``src/blindfold/`` may import this package — enforced by
``tests/test_devtools_absence.py``.
"""
