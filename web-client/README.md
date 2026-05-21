# unwind web-client

Source code of the DAG explorer SPA shipped under
`src/unwind/web/_static/` and served by FastAPI when you call
`project.show()`.

Stack: **Vite 5 + TypeScript + React 18 + @xyflow/react 12 + @dagrejs/dagre**.

## Install

```bash
bun install
```

## Dev workflow

In one terminal, start a Python project with the existing UI bound to its
default port `8765`:

```bash
cd ../example
uv run python main.py   # blocks on project.show()
```

In another:

```bash
cd web-client
bun run dev            # http://localhost:5173 with HMR, /api proxied to :8765
```

## Tests

```bash
bun run test         # vitest run (unit tests)
bun run typecheck    # tsc --noEmit
```

Tests live under `tests/`. They run against the source under `src/`.

## Production build

```bash
bun run build
```

This runs `tsc --noEmit` then `vite build`. The output goes straight to
`../src/unwind/web/_static/`. The bundle is committed in git, so a fresh
`uv sync` of the Python project is enough — Node is only needed when you
edit the frontend.

`web-client/public/__init__.py` is copied verbatim into `_static/`
because `_static` is a Python sub-package and `emptyOutDir: true` would
otherwise wipe it on every build.

## Layout

```
src/
  main.tsx           ← React root
  App.tsx            ← top-level layout (header + main)
  api.ts             ← typed fetch wrappers
  types.ts           ← DAG / Model / Column / TraceNode types
  styles.css         ← global CSS
  graph/
    Graph.tsx        ← <ReactFlow> + Controls + MiniMap + Background
    ModelNode.tsx    ← custom node component (kind-coloured bar + label)
    layout.ts        ← dagre wrapper, positions nodes left-to-right
    kinds.ts         ← per-kind palette (raw/ref/int/fct/...)
  panel/             ← right-hand panel : columns, SQL, data
  cell/              ← cell modal : trace tree + LLM investigate (SSE)
  api.ts             ← typed fetch wrappers (incl. async-iterable SSE)
  types.ts           ← DAG / Model / Trace / Explanation typings
tests/
  layout.test.ts     ← dagre layout output
  api.test.ts        ← SSE parser (mocks fetch)
```
