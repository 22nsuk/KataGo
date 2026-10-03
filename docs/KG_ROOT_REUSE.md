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
unchanged by a first-ply mask. A root visit-cap snapshot is rebuilt for the changed
support; snapshots below root remain valid. Masked child weight is excluded from
the capped root's deficit calculation.

Once a mask transition retains the tree, symmetry pruning remains disabled at that
root until the tree is cleared or the position changes. This keeps an explored
representative from being deleted when a later mask chooses another symmetry.
It can increase the number of root children and memory used at that position.

A change involving a depth greater than one clears the tree, including the change
back to a first-ply or empty restriction. Requests without `reuseRootTree true`
keep the usual clearing behavior when restriction arrays change. Normal invalidations
such as a position/player change, different search settings that require clearing,
komi/rule changes, and `clear_cache` still apply. Each request specifies the complete
desired restriction; omitted restrictions clear that player's previous mask.

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
real GTP process and saves `result.json`, `gtp.log`, and `stderr.log`. It checks the
capability, allow/avoid/clear transitions, per-color masks, retained visits, reallowed
children, low-policy moves, pass, excluded focus targets, deeper restriction
invalidation, default legacy clearing, ownership shape/range, and root aggregate
weight. Native NN-less search tests additionally verify the allowed 9:1 distribution
with a hidden 10000-visit child and Japanese dame-filling pass selection. The CI
workflow tests both colors with one and four threads and additional capped searches.

Windows CUDA build instructions are in [KG_CUDA13_WINDOWS.md](KG_CUDA13_WINDOWS.md).
