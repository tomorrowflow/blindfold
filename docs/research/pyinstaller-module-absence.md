# Asserting module absence in a frozen PyInstaller binary

Research note for issue #244: how to assert, mechanically and in CI, that the frozen
`blindfold-proxy` onefile binary contains no `blindfold_devtools` module.

## Provenance and scope

Every claim below is tagged with how it was established:

- **[src]** — read from the PyInstaller source actually pinned by this repo.
- **[doc]** — PyInstaller's own documentation at the matching version tag.
- **[exp]** — verified experimentally by building and running real onefile binaries.
- **[unverified]** — could not be established; stated as unknown rather than inferred.

Versions the claims hold for:

| Component | Version |
| --- | --- |
| PyInstaller | **6.21.0** (`uv.lock`; `pyproject.toml` declares `pyinstaller>=6.21` in the `freeze` group) |
| pyinstaller-hooks-contrib | 2026.6 (pulled in as a hard dependency of PyInstaller) |
| Python | 3.13 |
| Platform used for the experiments | macOS 26 / arm64 |

Experiments were run in a throwaway venv with PyInstaller 6.21.0, building five minimal
onefile specs (referred to below as **A–E**) that differ only in how a decoy top-level
package named `decoy` relates to the entry point. Sources are not committed; the specs
are reproduced inline in [§6](#6-reproducing-the-experiments) so the results can be
re-derived.

> **Caution on the version pin.** `pyproject.toml` pins `pyinstaller>=6.21`, i.e. it
> floats. Several of the interfaces below are internal or documented inaccurately, so a
> gate built on them should pin PyInstaller to an exact version (or at least `~=6.21`)
> and treat a PyInstaller bump as a change that must re-run the gate's own self-test
> ([§3](#3-is-excludes-verifiable-as-effective)).

---

## 1. What PyInstaller exposes for inspecting a built binary

### 1.1 `pyi-archive_viewer` — documented, but the documentation is wrong in 6.21.0

`pyi-archive_viewer` is a console script installed by the wheel
(`pyinstaller-6.21.0.dist-info/entry_points.txt` →
`PyInstaller.utils.cliutils.archive_viewer:run`) **[src]**. It is documented under
"Inspecting Archives" in *Advanced Topics*
(<https://pyinstaller.org/en/v6.21.0/advanced-topics.html>), which states it works with
"any archive built with PyInstaller (a `PYZ` or `PKG`), or any executable" **[doc]**.

The documentation at the `v6.21.0` tag
(<https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.21.0/doc/advanced-topics.rst>)
lists the options as **[doc]**:

- `-l, --log` — "Quick contents log."
- `-b, --brief` — "Print a python evaluable list of contents filenames."
- `-r, --recursive` — "Used with -l or -b, applies recursive behaviour."

The shipped 6.21.0 implementation disagrees on two of the three
(`PyInstaller/utils/cliutils/archive_viewer.py`, lines 221–252) **[src]**:

- the flag is `-l, --list` ("List the archive contents and exit"), **not** `--log`;
- `-b, --brief` prints one bare name per line prefixed by a space — it is **not** a
  Python-evaluable list.

Confirmed at the CLI **[exp]**:

```
$ pyi-archive_viewer --help
usage: pyi-archive_viewer [-h] [-l] [-r] [-b] [--log-level LEVEL] pyi_archive
  -l, --list         List the archive contents and exit (default: False).
  ...
$ pyi-archive_viewer --log dist/caseA
pyi-archive_viewer: error: argument --log-level: invalid choice: 'dist/caseA'
```

`--log` is not merely renamed: argparse resolves it as an abbreviation of `--log-level`,
so the *documented* invocation silently mis-parses its positional argument.

**Verdict:** documented, but the documentation does not match the shipped code in the very
version this repo pins, and the human-readable output format is not specified anywhere.
Usable in CI only if you pin the PyInstaller version and parse defensively. Do not build a
gate on the documented flag names.

Output format actually produced by `-l -r` (non-brief) **[exp]**:

```
Options in 'caseC' (PKG/CArchive):
 pyi-contents-directory _internal
Contents of 'caseC' (PKG/CArchive):
 position, length, uncompressed_length, is_compressed, typecode, name
 7894240, 33, 25, 1, 'x', 'decoy/__init__.py'
 ...
Contents of 'PYZ.pyz' (PYZ):
 typecode, position, length, name
 1, 311928, 108, 'decoy'
```

Note that PKG names are **filesystem paths** and PYZ names are **dotted module names** —
a matcher has to handle both forms (see [§5](#5-how-to-write-the-check-so-it-cannot-pass-vacuously)).

### 1.2 `PyInstaller.archive.readers` — internal, but it is what PyInstaller's own tests use

`PyInstaller/archive/readers.py` provides `CArchiveReader` (with `.toc`, `.extract()`,
`.open_embedded_archive()`) and re-exports `ZlibArchiveReader` from the runtime loader
package. Its module docstring says: *"Python-based CArchive (PKG) reader implementation.
**Used only in the archive_viewer utility.**"* **[src]**

The same file defines a ready-made helper **[src]**:

```python
def pkg_archive_contents(filename, recursive=True):
    """
    List the contents of the PKG / CArchive. If `recursive` flag is set (the default), the contents of the embedded PYZ
    archive is included as well.

    Used by the tests.
    """
```

TOC entry shapes (stable within 6.21.0) **[src]**:

- `CArchiveReader.toc` → `{name: (offset, length, uncompressed_length, compression_flag, typecode)}`
- `ZlibArchiveReader.toc` → `{name: (typecode, position, length)}`

Typecodes — CArchive/PKG (`readers.py`, lines 25–35) **[src]**: `b` binary, `d` runtime
dependency, `z` embedded PYZ, `Z` zipfile, `M` Python package, `m` Python module,
`s` Python script, `x` data, `o` runtime option, `l` splash. PYZ
(`loader/pyimod01_archive.py`, lines 29–32) **[src]**: `0` module, `1` package,
`2` data (deprecated), `3` PEP-420 namespace package.

**Verdict:** internal API — no stability guarantee, and the docstring says as much. I found
**no** statement anywhere in PyInstaller's documentation offering API-stability guarantees
for these modules **[unverified — absence of evidence, not evidence of a guarantee]**.
Against that: PyInstaller's own test suite depends on `pkg_archive_contents`, so it is
unlikely to vanish without a replacement, and the on-disk archive format is a bootloader
ABI, which changes rarely and visibly. This is the interface I recommend, with a pinned
PyInstaller and a self-test ([§3](#3-is-excludes-verifiable-as-effective)) that fails loudly
if the reader ever stops returning what we expect.

### 1.3 `build/<name>/*.toc` — internal build cache, not a report

The `.toc` files under the work path (`Analysis-00.toc`, `PYZ-00.toc`, `PKG-00.toc`,
`EXE-00.toc`) are PyInstaller's incremental-build *guts* cache, written and compared by
`Target._check_guts` / the `_GUTS` tuple in `building/build_main.py` (lines 586–612) **[src]**.
They are not documented in the user documentation at all (the docs describe only
`warn-*.txt`, `xref-*.html` and `graph-*.dot` as build-path artifacts,
<https://pyinstaller.org/en/v6.21.0/when-things-go-wrong.html>) **[doc]**.

They are actively misleading for an absence check: `Analysis-00.toc` contains the
*inputs*, including the `excludes` list itself. In experiment **B** (module unreachable,
`excludes=['decoy']`) `grep -c decoy Analysis-00.toc` returns 1 — the exclude entry — while
the binary contains nothing **[exp]**. A naive `grep` gate on `.toc` files produces false
failures.

**Verdict:** do not assert against `build/*.toc`.

### 1.4 `warn-<name>.txt` — a debug artifact, but the *only* place a suppressed import shows up

Documented: *"Analysis also puts messages in a warnings file named
`build/<name>/warn-<name>.txt` in the `work-path=` directory"* **[doc]**. Written by
`Analysis._write_warnings()` from `graph.make_missing_toc()`
(`building/build_main.py`, lines ~1060–1090) **[src]**. Its own header says
*"This file lists modules PyInstaller was not able to find"* and *"IMPORTANT: Do NOT post
this list to the issue-tracker."* **[exp]** — i.e. it is framed as a human debugging aid.

The line format is fixed in code as
`{status} module named {n} - imported by {importers}` **[src]**, and for an excluded module
`status` is literally `excluded`. Observed **[exp]**:

```
excluded module named decoy - imported by .../app_clean.py (top-level)
```

This turns out to be the single most valuable diagnostic for issue #244, because it is the
*only* artifact that distinguishes "the exclude was load-bearing" from "the exclude was a
no-op":

| Experiment | reachable? | `excludes` | mention in `warn-*.txt` | in binary |
| --- | --- | --- | --- | --- |
| **A** | yes (`try: import decoy`) | `['decoy']` | `excluded module named decoy - imported by app.py (optional)` | no |
| **B** | no | `['decoy']` | *(nothing)* | no |
| **D** | via `hiddenimports=['decoy']` | `['decoy']` | `excluded module named decoy - imported by app_clean.py (top-level)` | no |
| **E** | via `hiddenimports=['decoy']` | `[]` | *(nothing)* | **yes** (PYZ) |

**[exp]** for all four rows.

**Verdict:** debug artifact, format not documented, but stable in 6.21.0 and uniquely able
to answer "did anything try to reach this module?". Use it as a *secondary* assertion, never
as the primary one, and only when read from the same build that produced the binary, with a
clean work path (see the staleness caveat in [§1.7](#17-staleness-of-build-artifacts)).

### 1.5 `xref-<name>.html` — documented, useless for a machine gate

Documented as *"an HTML file that lists the full contents of the import graph, showing which
modules are imported by which ones"* **[doc]**; written by `Analysis._write_graph_debug()`
**[src]**. In experiment **A** the string `decoy` appears three times in the xref even
though the module is absent from the binary **[exp]** — the xref renders excluded/missing
nodes too. Same failure mode as `.toc`: false positives. Its HTML structure is generated by
`graph.create_xref()` and is not a documented schema.

**Verdict:** diagnostics for a human. Not a gate.

### 1.6 `--log-level` / `graph-<name>.dot` / `--debug=imports`

`--log-level` is documented as *"Amount of detail in build-time console messages. LEVEL may
be one of TRACE, DEBUG, INFO, WARN, DEPRECATION, ERROR, FATAL"* **[doc]**. At `DEBUG`,
`Analysis` additionally writes `build/<name>/graph-<name>.dot` **[src]**, which the docs
warn are *"very large"* **[doc]**. Log lines like `Excluding module 'X'` and
`Hidden import %r already found` exist **[src]** but are `logger.debug`/`logger.info`
free text with no stability contract.

`--debug=imports` passes `-v` to the embedded interpreter at *runtime* **[doc]**, which
reports what actually got imported during a run — useful evidence, but it only covers the
code paths that run.

**Verdict:** all three are diagnostics. Do not parse build logs in a gate.

### 1.7 Staleness of build artifacts

`Analysis` is a cached `Target`: it is re-run only when its guts change, per the `_GUTS`
tuple **[src]**. When it is skipped, `warn-*.txt` and `xref-*.html` are *not* rewritten,
because they are produced inside `Analysis.assemble()`. I was **unable to trigger a cached
(skipped) `Analysis`** for the experiment specs — every rebuild logged
`INFO: Building because excludes changed` and regenerated both files **[exp]** — so I cannot
state from observation how often the skip path fires in practice **[unverified]**. The safe
rule follows from the code regardless: **any gate that reads `build/` artifacts must build
with `--clean` into a fresh work path**, and must read them from the same invocation that
produced the binary it is gating.

---

## 2. Binary vs Analysis graph

**Assert against the built binary.** The Analysis graph is one input to the artifact, not the
artifact.

What an **Analysis-graph** assertion (inspecting `a.pure` / `a.binaries` / `a.datas` from
inside the spec, or the xref) misses:

- **`datas` never passes through the module graph at all.** `Analysis.assemble()` initialises
  `self.datas` from `self._input_datas` and later appends `graph.make_hook_datas_toc()`
  **[src]**; `excludes` is only ever handed to `initialize_modgraph()` (`build_main.py`
  line 676) **[src]**. A `.py` file placed in `datas` is therefore invisible to the graph and
  to `excludes`. This is not theoretical — see [§4.1](#41-datas-the-vector-that-defeats-excludes).
- **Modules can leave the PYZ after the graph is built.** `module_collection_mode` `'py'` /
  `'pyc'` / `'pyz+py'` causes `Analysis.assemble()` to append the module's source or
  byte-code to `self.datas` at destination `name.replace('.', os.sep)`
  (`build_main.py`, lines ~890–930) **[src]**. A graph check that looks at "pure modules"
  and a binary check that looks only in the PYZ both miss these.
- **Post-Analysis spec edits.** `EXE(...)` is passed `a.scripts, a.binaries, a.datas`
  explicitly (see `packaging/blindfold-proxy.spec`); a spec is arbitrary Python and can
  mutate those lists after `Analysis` returns. Only the binary reflects what was actually
  written.
- **It proves a step, not the release.** The graph is computed on the build machine at build
  time; the artifact that ships is the file. If the release pipeline ever rebuilds, re-signs,
  repacks, or substitutes the binary (Blindfold's macOS `.app` and Windows tray both embed
  this binary), only a binary-level check covers the thing that ships.

What a **binary** assertion misses, and why you still want the graph artifacts alongside it:

- **Attribution.** The binary tells you *that* `blindfold_devtools` is present; it cannot tell
  you *why*. `warn-*.txt` and `xref-*.html` name the importer.
- **The suppressed-reachability signal.** With `excludes` set, a new import edge to
  `blindfold_devtools` does **not** change the binary at all — it only appears in
  `warn-*.txt` as `excluded module named ...` (experiments A/D vs B) **[exp]**. A
  binary-only gate is blind to that regression.
- **Cost/latency.** The graph is available before EXE assembly. Immaterial here: the binary
  already has to be built for the `platform-verify` gate.

**Recommendation:** binary check is the gate; `warn-*.txt` is a second, narrower assertion
plus the diagnostic used when the gate fires.

---

## 3. Is `excludes=[...]` verifiable as *effective*?

### What `excludes` actually does

`--exclude-module` is documented as *"Optional module or package (the Python name, not the
path name) that will be ignored (as though it was not found)"* **[doc]**. In the code,
`Analysis.assemble()` passes `excludes` to `initialize_modgraph()` **[src]**, which forwards
them to `ModuleGraph.__init__`, where each excluded name becomes a lazy node mapped to
`None` under the comment `# excludes is stronger than implies`
(`lib/modulegraph/modulegraph.py`, lines 920–931) **[src]**. On first lookup, `findNode()`
materialises it as an `ExcludedModule`, a subclass of `BadModule`
(lines ~1135–1145, 594) **[src]**. `BadModule` types are not in
`PURE_PYTHON_MODULE_TYPES`, so `make_pure_toc()` never emits them **[src]**.

Consequences, all confirmed experimentally:

- **`excludes` beats `hiddenimports`.** `add_hiddenimports()` calls `find_node(modnm)`, which
  returns the already-materialised `ExcludedModule` and logs
  `Hidden import %r already found` **[src]**. Experiment **D**
  (`excludes=['decoy']` + `hiddenimports=['decoy']`) → absent from the binary; the frozen app
  reports `find_spec(decoy) -> None` **[exp]**.
- **`excludes` does *not* govern `datas`.** Experiment **C** — see
  [§4.1](#41-datas-the-vector-that-defeats-excludes) **[exp]**.

### Is it vacuous?

Yes, in exactly the way the issue suspects — but the mechanism is worth stating precisely,
because the obvious framing is slightly off.

The literal reading ("an unreachable module makes the absence check pass for the wrong
reason") is true but understates the problem. The sharper statement is:

> **Once `excludes=['blindfold_devtools']` is set, no module-graph regression can ever make a
> binary-contents check fail.** `excludes` converts "devtools became reachable from the entry
> point" from a build-artifact difference into a silent runtime `ImportError`. The absence
> check keeps passing — correctly, but for a reason that has nothing to do with the code
> change you wanted it to catch.

Experiments B and D both pass the absence check. In B nothing ever referenced `decoy`; in D a
`hiddenimports` entry explicitly demanded it. The binary is identical in the respect being
gated. The check cannot distinguish them **[exp]**. That is the vacuity.

Two things follow:

1. **`excludes` is not what makes the guarantee, and the absence check does not verify
   `excludes`.** With `excludes` in place, the check is only exercising the *non-graph*
   vectors of [§4](#4-known-ways-a-module-re-enters-a-frozen-build). That is still worth
   gating — those are the vectors that bypass `excludes` — but it must be understood as the
   check's real scope.
2. **A passing check is meaningless without a positive control.** The gate must include a
   deliberately-poisoned build proving the detector detects. See
   [§5](#5-how-to-write-the-check-so-it-cannot-pass-vacuously).

---

## 4. Known ways a module re-enters a frozen build

Ordered by how likely they are to defeat a naive check.

### 4.1 `datas` — the vector that defeats `excludes`

**Proven end-to-end.** Experiment **C**: `excludes=['decoy']` *and*
`datas=[('decoy/__init__.py', 'decoy')]`. Result **[exp]**:

```
$ pyi-archive_viewer -l -r distC/caseC | grep decoy
 7894240, 33, 25, 1, 'x', 'decoy/__init__.py'

$ ./distC/caseC
sys.path ['.../_MEIxxxx/base_library.zip', '.../_MEIxxxx/python3.13/lib-dynload', '.../_MEIxxxx']
find_spec(decoy) -> ModuleSpec(name='decoy',
    loader=<_frozen_importlib_external.SourceFileLoader object at 0x...>,
    origin='.../_MEIxxxx/decoy/__init__.py', ...)
IMPORTED decoy from .../_MEIxxxx/decoy/__init__.py decoy-present
```

The excluded module is bundled, importable, and imports successfully in the frozen onefile
binary. Two mechanisms make this work:

- `sys._MEIPASS` is on `sys.path` at runtime **[exp, and src]** — the loader comment reads
  *"we refrained from adding `sys._MEIPASS` to `sys.path` until our importer hooks is in
  place"* (`loader/pyimod02_importers.py`, line ~679) **[src]**.
- `PyiFrozenFinder` deliberately falls back to Python's `FileFinder`, documented in-source as
  covering *"extension modules and modules that are collected only as source .py files"*
  (`loader/pyimod02_importers.py`, `_find_fallback_spec`) **[src]**.

**Any `.py` or `.pyc` that lands in the bundle at an importable path is importable, regardless
of `excludes`.**

### 4.2 `datas=collect_data_files("blindfold")` — precisely what this spec sweeps

`packaging/blindfold-proxy.spec` uses `datas=collect_data_files("blindfold")`. From the
6.21.0 implementation (`utils/hooks/__init__.py`) **[src]** and its docstring **[doc,
identical text at <https://pyinstaller.org/en/v6.21.0/hooks.html>]**:

Signature: `collect_data_files(package, include_py_files=False, subdir=None, excludes=None, includes=None)`.

**What it does sweep:**

- It resolves the package via `get_all_package_paths(package)` → `importlib.util.find_spec`,
  taking `spec.submodule_search_locations` **[src]**. For a top-level name this runs in the
  build process without importing the package; for a dotted name it runs in an isolated
  subprocess **[src]**. So the sweep root is *the `blindfold` package directory as resolved
  on the build machine* — here `src/blindfold/` (the spec puts `src/` on `pathex`).
- It globs `**/*` under that directory, recursively, including every subpackage directory
  **[src]**.
- It emits `(source, dest)` pairs preserving the path relative to the package base, so files
  land at `blindfold/<relpath>` in the bundle **[src]**. This is how `ui_dist`, the cold-start
  seed, `l3_stopwords_en_de.txt` and `seeded_allowlist.txt` get collected.

**What it does not sweep:**

- **Anything outside `src/blindfold/`.** A sibling top-level package `src/blindfold_devtools/`
  is not touched by `collect_data_files("blindfold")` **[src]** — the glob root is the
  `blindfold` package directory only.
- **`.py` and `.pyc` files**, because with `include_py_files=False` the function appends
  `['**/*' + s for s in compat.ALL_SUFFIXES]` to its exclude list **[src]**, and
  `compat.ALL_SUFFIXES = importlib.machinery.all_suffixes()` **[src]**, i.e. `.py`, `.pyc`
  and the platform extension-module suffixes. It also unconditionally appends
  `'**/__pycache__/*.pyc'` **[src]**.

**The residual risk this leaves — state it explicitly:**

- If devtools ever lives *under* `src/blindfold/` (e.g. `src/blindfold/devtools/`), its
  **non-`.py` payload is swept in automatically and silently** — fixtures, `.json`, `.sql`,
  `.sh`, `.txt`, `.zip`, `.pyz`. The spec's own comment advertises this ("a future vendored
  data file needs no matching edit here"); that generosity is the hazard. The `.py` files
  would still be filtered out, so it would not be importable as a package, but the data would
  ship.
- The suffix filter is **extension-based, not content-based**. `helper.py.txt`,
  `payload.pyz`, or a `.dylib`/`.dll` (not in `all_suffixes()` on any platform) are collected.
  Collected `.dylib`/`.dll` files are then reclassified `DATA → BINARY` by the
  binary-vs-data reclassification pass in `Analysis.assemble()` **[src]**.
- The sweep reflects the **build machine's** `src/blindfold/` tree, not git. Stray files in a
  dirty checkout ship. Blindfold's `platform-verify` gate builds from a clean checkout, which
  mitigates this **[unverified — I did not read the workflow's checkout semantics in detail]**.

### 4.3 `hiddenimports`

Documented as *"Name an import not visible in the code of the script(s)"* **[doc]**. Adds a
node and an edge from the top-level script **[src]**. Experiment **E** (`hiddenimports=['decoy']`,
no excludes) → `decoy` present in the PYZ, `find_spec` resolves it in the frozen app via
`PyiFrozenLoader` **[exp]**. Caught by a PYZ-inclusive binary check. Beaten by `excludes`
(experiment D) **[exp]**.

### 4.4 Hooks — including hooks injected by *any installed distribution*

Hook directories are discovered at build time via the `pyinstaller40` / `hook-dirs`
entry-point group, in an isolated subprocess (`build_main.discover_hook_directories()`)
**[src]**. `pyinstaller-hooks-contrib` (2026.6 here) is a hard dependency of PyInstaller and
supplies hooks last, so packages providing their own take priority **[src]**.

The consequence for a gate: **the set of hooks that run is a function of what is installed in
the build environment**, not of anything in this repo. Adding a dependency — or a dependency
adding a `pyinstaller40` entry point in a patch release — can change what is bundled with no
diff to `packaging/blindfold-proxy.spec`. A hook can add modules via the `hiddenimports`
global, via `datas`/`binaries` globals, or at runtime via
`hook_api.add_imports()` / `add_datas()` / `add_binaries()` **[doc]**. Hook-supplied datas are
merged in `Analysis.assemble()` via `graph.make_hook_datas_toc()` **[src]** — i.e. they land in
`datas` and therefore inherit [§4.1](#41-datas-the-vector-that-defeats-excludes)'s bypass of
`excludes`.

`excludedimports` in a hook is the mirror image and is *weaker* than `excludes`: *"If an
excluded module is imported only by the hooked module or one of its sub-modules, the excluded
name and its sub-modules will not be part of the bundle"* — but if imported elsewhere it
remains **[doc]**. There is long-standing friction here (hooks' `excludedimports` overriding
explicit user `hidden-import`s): see
[pyinstaller#1669](https://github.com/pyinstaller/pyinstaller/issues/1669),
[#1584](https://github.com/pyinstaller/pyinstaller/issues/1584),
[#1901](https://github.com/pyinstaller/pyinstaller/issues/1901). Those issues are from the
2015–2016 era and I did **not** verify that their specific behaviours still hold in 6.21.0
**[unverified]**; they are cited only as evidence that exclusion semantics are historically
contested, which argues against making `excludes` the load-bearing guarantee.

### 4.5 `collect_submodules` / `collect_all` / `copy_metadata`

- `collect_submodules(package)` returns a list *"to be assigned to `hiddenimports` in a hook"*
  **[doc]** — so it re-enters through [§4.3](#43-hiddenimports), and would be blocked by
  `excludes`. Its danger is breadth: it pulls in *everything* under a package, so if devtools
  ever became `blindfold.devtools`, a `collect_submodules("blindfold")` anywhere would sweep it.
- `collect_all(package)` returns `(datas, binaries, hiddenimports)` **[doc]** — combines
  [§4.2](#42-datascollect_data_filesblindfold--precisely-what-this-spec-sweeps) and
  [§4.3](#43-hiddenimports), so part of its output bypasses `excludes`.
- `copy_metadata(package)` collects `.dist-info` metadata *as datas* **[doc]**. It ships
  **metadata, not code** — `entry_points.txt`, `RECORD`, `METADATA`. It cannot by itself make a
  module importable. It is still relevant to issue #244 for a different reason: a shipped
  `entry_points.txt` advertising `blindfold_devtools:...` is an information leak about the
  devtools surface, and any runtime code doing entry-point discovery will then try to import
  a module that is not there. Worth including in the gate's match set as a warning.

### 4.6 `module_collection_mode` — modules that leave the PYZ

A hook or spec can set `module_collection_mode` to `'pyz'`, `'pyc'`, `'py'`, `'pyz+py'` or
`'py+pyz'`, or call `hook_api.set_module_collection_mode(name, mode)` **[doc]**. For `'py'`
and `'pyc'`, `Analysis.assemble()` appends the source or compiled file to `self.datas` at
destination `name.replace('.', os.sep)` **[src]** — i.e. `blindfold_devtools/foo.py` at the
bundle root, which is importable per [§4.1](#41-datas-the-vector-that-defeats-excludes).
`--noarchive` has the same effect globally: *"instead of storing all frozen Python source
files as an archive inside the resulting executable, store them as files in the resulting
output directory"* **[doc]**, implemented as `_ModuleCollectionMode.PYC` **[src]**.

**A check that only reads the PYZ TOC misses all of these.**

### 4.7 `base_library.zip`

`Analysis.assemble()` builds `base_library.zip` from `graph._base_modules` and bundles it as a
single `DATA` entry **[src]**; its members are therefore invisible in both the PKG and PYZ
TOCs. `PyiModuleGraph._analyze_base_modules()` populates it strictly from the hard-coded
`PY3_BASE_MODULES` list plus their submodules **[src]**, and the built zip contained 154
stdlib `.pyc` entries (`types.pyc`, `codecs.pyc`, `abc.pyc`, …) **[exp]**. So a third-party
package cannot land there — but scanning it costs nothing and closes the hole by construction.

### 4.8 Vectors identified but not tested

Listed for completeness; I did not build a case for these **[unverified]**:

- `Tree()` and `--add-data` pointed at a directory (same class as [§4.1](#41-datas-the-vector-that-defeats-excludes); no reason to expect different behaviour).
- `runtime_hooks` / `--runtime-hook` (arbitrary code in the frozen app; a runtime hook could
  extend `sys.path` to a bundled directory).
- Namespace packages split across multiple locations (`get_all_package_paths` explicitly
  handles PEP 420 multi-location packages **[src]**), which could give
  `collect_data_files("blindfold")` more than one sweep root.
- Nested `Z` (zipfile) archive entries — `CArchiveReader.open_embedded_archive` raises
  `NotAnArchiveError("Zipfile archives not supported yet!")` for typecode `Z` **[src]**, so a
  reader-based check cannot look inside one. PyInstaller ≥ 6.0 no longer produces them
  (`zipfiles`/`zipped_data` are *"always empty"* since v6.0 **[src]**), but a check should
  fail loudly rather than skip if it ever encounters one.

---

## 5. How to write the check so it cannot pass vacuously

Four layers. Layers 1 and 2 are the assertion; layer 3 is what makes a pass mean something;
layer 4 catches the regression that `excludes` would otherwise absorb.

### Layer 1 — static containment check on the release binary

Match on **both** name forms, because PKG entries are paths and PYZ entries are dotted names,
and match at a **name boundary** so `blindfold_devtools_helper` is caught but a hypothetical
`blindfold_devtoolsomething` is not silently ignored.

```python
"""Assert a top-level module name is absent from a PyInstaller onefile binary.

Verified against PyInstaller 6.21.0. `PyInstaller.archive.readers` is an INTERNAL
API (its docstring says "Used only in the archive_viewer utility"); pin PyInstaller
exactly and rely on the self-test in layer 3 to catch a breaking change.
"""
from __future__ import annotations

import io
import sys
import zipfile

from PyInstaller.archive.readers import CArchiveReader


def _module_hit(entry_name: str, module: str) -> bool:
    """Dotted-name match: 'M' or 'M.sub'."""
    return entry_name == module or entry_name.startswith(module + ".")


def _path_hit(entry_name: str, module: str) -> bool:
    """Filesystem-path match: 'M/...', 'M.py', 'M.pyc', 'M<ext>' at the top level."""
    norm = entry_name.replace("\\", "/")          # PKG dests use os.sep at build time
    head = norm.split("/", 1)[0]
    return head == module or head.startswith(module + ".")


def find_hits(binary: str, module: str) -> list[str]:
    hits: list[str] = []
    pkg = CArchiveReader(binary)
    for name, (*_, typecode) in pkg.toc.items():
        if typecode in ("m", "M", "s"):           # modules/scripts in PKG (noarchive path)
            if _module_hit(name, module):
                hits.append(f"PKG[{typecode}] {name}")
        elif typecode == "z":                     # embedded PYZ
            pyz = pkg.open_embedded_archive(name)
            hits += [f"PYZ({name}) {m}" for m in pyz.toc if _module_hit(m, module)]
        elif typecode == "Z":                     # nested zipfile: cannot inspect -> fail
            raise RuntimeError(f"unsupported nested zip archive entry {name!r}")
        else:                                     # 'b' binaries, 'x' data, ...
            if _path_hit(name, module):
                hits.append(f"PKG[{typecode}] {name}")
            if name.endswith("base_library.zip"):
                with zipfile.ZipFile(io.BytesIO(pkg.extract(name))) as zf:
                    hits += [f"base_library.zip {n}" for n in zf.namelist()
                             if _path_hit(n, module)]
    return hits


if __name__ == "__main__":
    binary, module = sys.argv[1], sys.argv[2]
    found = find_hits(binary, module)
    if found:
        print(f"FAIL: {module!r} present in {binary}:")
        for h in found:
            print("  " + h)
        raise SystemExit(1)
    print(f"OK: {module!r} absent from {binary}")
```

Validated against all five experiments **[exp]**:

```
caseA: OK   (reachable, excluded)                    rc=0
caseB: OK   (unreachable, excluded)                  rc=0
caseC: FAIL PKG[x] decoy/__init__.py                 rc=1   <- excludes bypassed via datas
caseD: OK   (hiddenimports + excludes)               rc=0
caseE: FAIL PYZ(PYZ.pyz) decoy                       rc=1   <- hiddenimports, no excludes
```

If the internal-API dependency is unacceptable, the same information is obtainable from
`pyi-archive_viewer -l -b -r <binary>` — but only with a pinned PyInstaller, using `--list`
(not the documented `--log`), and with a parser tolerant of the leading-space output format
([§1.1](#11-pyi-archive_viewer--documented-but-the-documentation-is-wrong-in-6210)).

### Layer 2 — dynamic importability check inside the frozen binary

Containment and importability are different properties, and importability is the one that
matters for a devtools-exclusion guarantee. Add a hidden self-check to the frozen entry point
(`packaging/blindfold_proxy_entry.py`) — e.g. `--assert-module-absent NAME` — that does:

```python
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec(name) is None else 1)
```

Why this is worth having in addition to layer 1:

- It uses **only the stdlib** — zero dependence on PyInstaller internals, so it survives any
  PyInstaller version bump.
- It covers PYZ, on-disk `.py`/`.pyc`, extension modules, and `base_library.zip` in one shot,
  because it asks the actual frozen import system.
- It is the check that would have caught experiment **C** without knowing anything about
  archive typecodes: `find_spec` returned a real `ModuleSpec` there and `None` in A/B/D **[exp]**.

Caveat: `find_spec` answers "importable", not "contained". A payload bundled at a
non-importable path passes layer 2 and fails layer 1 — which is why both layers exist.
Also note `find_spec` on a *dotted* name imports the parent package; use a top-level name.

Blindfold's `platform-verify` gate already builds and runs on hosted macOS/Windows/Linux
runners (ADR-0042), so running the binary is available.

### Layer 3 — positive control (this is the anti-vacuity mechanism)

**A green absence check proves nothing unless the same check is shown to go red.** In the same
CI job, build a *canary* binary from a copy of the release spec with two edits — `excludes`
emptied and `hiddenimports=["blindfold_devtools"]` added — and assert that layers 1 and 2
**fail** on it. Optionally add a second canary that keeps `excludes` and smuggles a
`blindfold_devtools/__init__.py` through `datas`, reproducing experiment **C**; that one
proves the check covers the vector `excludes` cannot.

This is the only construction that rules out all the ways the gate can pass for the wrong
reason: a typo'd module name, a matcher that never matches, an archive-reader API that changed
shape under a PyInstaller bump, a `find_spec` call that was silently swallowed, or the module
simply never having been reachable.

Concretely, the gate should be a test that asserts *both* directions:

```
assert check(release_binary, "blindfold_devtools") == ABSENT
assert check(canary_binary,  "blindfold_devtools") == PRESENT   # fails the build if it doesn't
```

### Layer 4 — non-vacuity of `excludes` itself (only if `excludes` is used)

If `packaging/blindfold-proxy.spec` gains `excludes=["blindfold_devtools"]`, then per
[§3](#3-is-excludes-verifiable-as-effective) a reachability regression becomes invisible to
layers 1–2 and shows up only as a runtime `ImportError`. Recover the signal by asserting on
the warn file from the *same* build:

```
grep -q "excluded module named blindfold_devtools" build/blindfold-proxy/warn-blindfold-proxy.txt  -> FAIL
```

A hit means production code now imports devtools and `excludes` silently deleted it.
Preconditions: build with `--clean` into a fresh work path
([§1.7](#17-staleness-of-build-artifacts)), and treat this string as version-fragile — it is a
debug artifact, not a contract.

**Alternative worth considering:** leave `excludes` empty and let layers 1–2 be the whole
guarantee. Then a reachability regression fails the gate *directly and with a clear message*,
instead of being converted into a runtime error that only a smoke test would find. The
trade-off is that a devtools import which slips past the gate would then actually ship. Given
that layer 3 makes the gate trustworthy, I lean toward **keeping `excludes` for defence in
depth and adding layer 4**, so that neither failure mode is silent.

---

## 6. Reproducing the experiments

Minimal reproduction with PyInstaller 6.21.0. `decoy/__init__.py` contains
`MARKER = "decoy-present"`. Two entry points: `app.py` (imports `decoy` inside
`try/except`) and `app_clean.py` (never mentions `decoy`); both print
`importlib.util.find_spec("decoy")`.

| Case | entry | `hiddenimports` | `datas` | `excludes` | result |
| --- | --- | --- | --- | --- | --- |
| A | `app.py` | — | — | `['decoy']` | absent; warn file records `excluded module named decoy` |
| B | `app_clean.py` | — | — | `['decoy']` | absent; warn file silent (vacuous exclude) |
| C | `app.py` | — | `[('decoy/__init__.py', 'decoy')]` | `['decoy']` | **present** as PKG `x` entry, imports successfully at runtime |
| D | `app_clean.py` | `['decoy']` | — | `['decoy']` | absent; warn file records `excluded module named decoy` |
| E | `app_clean.py` | `['decoy']` | — | `[]` | **present** in PYZ, `find_spec` resolves via `PyiFrozenLoader` |

Each spec is otherwise the stock onefile shape:

```python
a = Analysis(['<entry>.py'], pathex=['.'], binaries=[], datas=<datas>,
             hiddenimports=<hiddenimports>, excludes=<excludes>,
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='case<X>', console=True)
```

Built with `pyinstaller case<X>.spec --distpath dist<X> --workpath work<X> -y --clean`.

---

## 7. Sources

Primary documentation (all fetched at the `v6.21.0` tag):

- <https://pyinstaller.org/en/v6.21.0/usage.html> — `--exclude-module`, `--hidden-import`, `--collect-*`, `--copy-metadata`, `--add-data`, `--log-level`, `--debug`, `--noarchive`
- <https://pyinstaller.org/en/v6.21.0/spec-files.html> — `Analysis` parameters and output members
- <https://pyinstaller.org/en/v6.21.0/when-things-go-wrong.html> — `warn-*.txt`, `xref-*.html`, `graph-*.dot`, `--debug=imports`
- <https://pyinstaller.org/en/v6.21.0/advanced-topics.html> — `pyi-archive_viewer`, PKG/PYZ structure, runtime module resolution, `sys._MEIPASS`
- <https://pyinstaller.org/en/v6.21.0/hooks.html> — `hiddenimports`, `excludedimports`, `collect_data_files`, `collect_submodules`, `copy_metadata`, `collect_all`, `module_collection_mode`
- <https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.21.0/doc/advanced-topics.rst> — the stale `-l, --log` option text
- <https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.21.0/doc/hooks.rst> — `module_collection_mode` values, `set_module_collection_mode`

Source read directly from the installed `pyinstaller==6.21.0` wheel:

- `PyInstaller/utils/cliutils/archive_viewer.py`
- `PyInstaller/archive/readers.py`
- `PyInstaller/loader/pyimod01_archive.py`, `PyInstaller/loader/pyimod02_importers.py`
- `PyInstaller/building/build_main.py`
- `PyInstaller/depend/analysis.py`
- `PyInstaller/lib/modulegraph/modulegraph.py`
- `PyInstaller/utils/hooks/__init__.py`
- `PyInstaller/compat.py`
- `pyinstaller-6.21.0.dist-info/entry_points.txt`

Issue tracker (cited as historical context only; **not** re-verified against 6.21.0):

- <https://github.com/pyinstaller/pyinstaller/issues/1669> — explicitly stated hidden imports get excluded by hooks
- <https://github.com/pyinstaller/pyinstaller/issues/1584> — `excludedimports` in hooks: modules not bundled at all
- <https://github.com/pyinstaller/pyinstaller/issues/1901> — refactor `excludedimports` processing

Repo files referenced:

- `packaging/blindfold-proxy.spec`
- `pyproject.toml` (`freeze` dependency group), `uv.lock` (`pyinstaller==6.21.0`)
- `.github/workflows/platform-verify.yml`
