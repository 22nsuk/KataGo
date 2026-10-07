# Root-only search-tree reuse

This fork implements an optional GTP analysis extension used by KG-next. The search
implementation, build, and native regression belong to this engine repository.

## Negotiation and request

Clients enable reuse only after the engine advertises `kg-reuse-root-tree` in
`list_commands` or responds `true` to `known_command kg-reuse-root-tree`. Clients
that need to identify the contract version can also send `kg-reuse-root-tree`,
which responds `root-only-v1`. That query takes no arguments and does not itself
enable reuse.

```text
kata-analyze B interval 10 rootInfo true reuseRootTree true allow b D4,F6 1
kata-analyze B interval 10 rootInfo true reuseRootTree true avoid b D4 1
kata-analyze B interval 10 rootInfo true reuseRootTree true
```

The same option is accepted by `kata-genmove_analyze`, `kata-search_analyze`, and
`kata-search_analyze_cancellable`. It defaults to `false`. Engines without the
capability should receive normal upstream analysis requests.

The JSON analysis engine does not implement this option. `cpp/command/analysis.cpp`
still calls `setAvoidMoveUntilByLoc` without the reuse flag, so allow/avoid changes
there clear the tree. KG-next enables reuse only on the GTP commands above, after
the capability query.

## Contract

With the position, player, and search configuration unchanged, a changed restriction
can retain the tree only when both the previous and next black/white restriction
arrays contain depths zero or one, or are empty. Excluded root children retain
their visits and descendants but contribute nothing to root selection, move
analysis, aggregate value/weight, ownership, or territory pass suppression. A cleared
restriction makes those children available again. A newly allowed legal move,
including a formerly symmetry-pruned move or pass, can receive new visits.

Root visit counts continue to include all previously invested root visits. Move
rows and aggregate weights reflect the currently allowed choices, so their counts
need not add up to the lifetime root visit count. Descendant rules and values are
unchanged by a first-ply mask. For a visit-capped root, changing the mask discards its
snapshot and gives normal selection another `visitCapContempt` counted visits before freezing
the distribution again. Previously unallocated moves are eligible during this interval;
as in a fresh capped search, a small cap does not guarantee every legal move a visit.
Only a change to the current root player's first-ply mask restarts this interval.
Repeated requests, changes confined to the other player's first-ply mask, and
empty/all-zero encodings preserve both an in-progress interval and an already frozen
snapshot. Both players' requested arrays are still stored. This equivalence does not
relax deeper-restriction invalidation or the default legacy clearing behavior.
Focus visits do not consume the interval. Snapshots below root remain valid. Masked child weight is
excluded from the capped root's deficit calculation.

When a mask transition retains the tree, symmetry pruning stays enabled only if the
new duplicate-location mask and valid symmetry list are identical to those used by
the retained tree. For example, excluding only pass or changing only the other
player's first-ply restrictions does not change board symmetry representatives.
Existing representative visits and their symmetric analysis rows then remain shared.

If that comparison changes, symmetry pruning is disabled until the tree is cleared
or the position changes, even after the restriction is removed. This protects all
retained children, including hidden ones, from being deleted as duplicate moves.
It can increase the number of root children and memory used at that position. A
formerly shared analysis row can then start an independent child with fewer visits;
transferring a representative's tree to another symmetric coordinate is not part of
this extension. A first restricted search with no existing tree keeps normal symmetry
pruning enabled. This distinction does not change visit-cap restart or search limits.

A change involving a depth greater than one clears the tree, including the change
back to a first-ply or empty restriction. Requests without `reuseRootTree true`
keep the usual clearing behavior when restriction arrays change. Normal invalidations
such as a position/player change, different search settings that require clearing,
komi/rule changes, and `clear_cache` still apply. Each request specifies the complete
desired restriction; omitted restrictions clear that player's previous mask.

## Search limits

Tree reuse does not change the existing meaning of search limits. `maxVisits` counts
retained visits, including visits to children currently hidden by the root mask;
`maxPlayouts` counts only new counted playouts in the current request. The visit-cap
interval described above controls when the root distribution freezes, not when the
search stops, and does not reset or extend either limit.

`kata-analyze` is continuous analysis and ignores the configured move-search limits.
In contrast, `kata-search_analyze`, `kata-search_analyze_cancellable`, and
`kata-genmove_analyze` obey them. If a retained tree has already reached `maxVisits`,
a bounded request can return without new playouts even after changing the mask.
To budget new work on each bounded request, configure `maxPlayouts` before starting
the analysis session and omit `maxVisits` or set it sufficiently high. All configured
limits still apply together. For example, `maxPlayouts = 1000` with no `maxVisits`
limit permits up to 1000 new counted playouts per request while preserving old visits.

## CPU validation

Build the actual checked-out engine source, without applying a separate patch:

```sh
cmake -S cpp -B build-eigen -G Ninja -DUSE_BACKEND=EIGEN -DCMAKE_BUILD_TYPE=Release
cmake --build build-eigen --parallel 2
(cd cpp && ../build-eigen/katago runtests)
(cd cpp && ../build-eigen/katago runoutputtests)
python3 python/probe_kg_root_reuse.py --engine build-eigen/katago \
  --model cpp/tests/models/g170-b6c96-s175395328-d26788732.bin.gz \
  --output proof --player W --threads 4 --ownership --root-visit-cap 32
```

Eigen3, zlib, and optionally libzip headers/libraries are needed. The probe uses a
real GTP process. `--output` must be a new directory; an existing directory is rejected
without overwriting it, so a failed rerun cannot inherit an old PASS. The probe saves
`gtp.cfg`, `gtp.log`, and `stderr.log`. It writes `result.json` with PASS only after all
checks, acknowledged `quit`, zero process exit, reader shutdown, and log writes succeed.
Failed runs do not have a new PASS result. The result records the engine, model, and
configuration SHA-256 hashes along with the engine version and test options; these
identify the tested inputs but are not a GPU performance or strength certification.
The final report drained by the stop barrier is checked for masked moves and visit
continuity as well as the earlier streamed reports. The probe checks the
capability, allow/avoid/clear transitions, per-color masks, retained visits, reallowed
children, mixed retained/new candidates, first-request symmetry pruning, low-policy
moves, pass, excluded focus targets, deeper restriction invalidation, default legacy
clearing, ownership shape/range, root aggregate weight, and continuous versus bounded
search limits. Native NN-less search tests additionally verify cap reformation after
fresh counted visits, unchanged-mask restarts, per-request playout budgets, the allowed
9:1 distribution with a hidden 10000-visit child, and Japanese dame-filling pass selection. The CI
workflow tests both colors with one and four threads and additional capped searches.
Equivalent-mask cap regressions cover both colors, one/four threads, tree search,
graph search with eval cache, preserved snapshots, in-progress cap intervals, and
legacy/deeper invalidation. GTP checks exercise empty-list and inactive-color requests
as well as komi, rule, and player invalidation of a warmed tree.
Symmetry regressions cover pass-only and inactive-color restrictions, excluding a
nonrepresentative, equivalent empty/all-zero masks, retained children and visits,
representative changes, sticky pruning disablement, and resets on clearing or playing
a move. Real-process checks also verify that unchanged representatives keep their
shared analysis rows and that excluded moves never appear in those rows.

### Probe capture and failure handling

Run the probe with assertions enabled. `-O`, `-OO`, and nonzero `PYTHONOPTIMIZE`
are rejected at module import, before output creation or engine startup. This applies
to imported use as well as the CLI; C++ Release builds are unaffected.

The existing `gtp.log` and `stderr.log` filenames and plain UTF-8 format are retained.
Logs are streamed to disk instead of accumulated in memory. Stdout and sent commands
share a locked transcript; stderr retains only its last 20 lines (up to 2000 characters
per line) in memory for diagnostics. The complete accepted lines still go to disk.
The stdout queue holds at most 16 lines, with backpressure rather than dropped reports.
Lines above 1,048,576 characters and non-analysis response text above 4,194,304 characters
fail explicitly instead of consuming unbounded memory. Analysis reports are parsed and
logged but not duplicated into command return strings, so slow bounded searches may
produce more total analysis than that response limit. These generous limits target this
probe's 9x9 protocol; they are not engine or GUI limits.

After the quit acknowledgement (or a failed quit), readers continue draining to the
logs without queuing more reports. Reader, decoding, write, flush, or log-close errors
prevent PASS. All checks, clean process exit, reader joins, and closed log files must
succeed before `result.json` is published. Failed runs retain the evidence captured
up to the failure; oversized or undecodable output is not silently accepted or
promised as a complete log. Streaming bounds capture memory, not total disk usage;
no analysis sampling, compression dependency, or changed search settings are introduced.

Probe lifecycle and final-report failure handling have a separate standard-library
test suite, run by the same CI before building the engine:

```sh
python3 -m unittest discover -s python -p test_probe_kg_root_reuse.py -v
```

Those tests inject protocol/cleanup failures; they do not replace the native engine
or real-process regression matrix above.

Windows CUDA build instructions are in [KG_CUDA13_WINDOWS.md](KG_CUDA13_WINDOWS.md).
