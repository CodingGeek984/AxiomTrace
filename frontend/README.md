# AxiomTrace — Frontend

The Next.js 16 studio that renders the AST graph, drives the sandbox and
exports LLM context. Runs in the browser during development and inside
Electron for the desktop build.

> **Working in this folder?** Read [`AGENTS.md`](./AGENTS.md) first. This
> Next.js release has breaking changes; the local docs under
> `node_modules/next/dist/docs/` are authoritative and supersede anything you
> remember from older versions.

## Scripts

| Command              | What it does                                                    |
| -------------------- | --------------------------------------------------------------- |
| `npm run dev`        | Next.js dev server on `http://localhost:3000`                   |
| `npm run build`      | Production build (`next build`, Turbopack)                      |
| `npm run start`      | Serve the production build                                      |
| `npm run lint`       | ESLint (`eslint-config-next/core-web-vitals`)                   |
| `npm test`           | Node test runner — tracer engine and playback state             |
| `npm run backend`    | Start the Flask API from `../backend`                           |
| `npm run electron`   | Launch the Electron shell                                       |
| `npm run electron:dev` | Next dev + Electron together, with process supervision        |

## Configuration

The studio reaches the API through one variable:

```bash
# .env.local
NEXT_PUBLIC_API_URL=http://127.0.0.1:5000/api/v1
NEXT_PUBLIC_TRACE_STREAM_URL=   # optional SSE/WebSocket trace feed
```

Both default sensibly when unset, so `npm run dev` works with a local backend
on port 5000.

## Structure

```
src/
├── app/                Routes, root layout, global theme
│   ├── layout.jsx      Fonts, color scheme, render error boundary
│   └── page.jsx        Shell: header, canvas, inspector, toasts
├── components/
│   ├── canvas/         GraphCanvas, CustomNode, DataTracer, Inspector
│   ├── modals/         ExportModal
│   └── ui/             Header, Toolbar, StatusBadge, TerminalLog,
│                       ErrorBoundary
├── config/theme.js     Design tokens - palette, type/status/busy metadata
├── hooks/              dagre layout helpers
├── lib/
│   ├── api.js          API client (the only network layer)
│   ├── graph.js        Backend graph <-> React Flow mapping
│   ├── samples.js      Bundled demo sources for one-click loading
│   ├── stream.js       Optional live trace transport
│   └── tracer.js       Topological trace engine + simulated payloads
├── tests/              Node test runner coverage for the tracer
└── store/useGraphStore.js
                        Zustand store - graph, selection, bounded
                        execution log (100), undo history (50), trace
                        playback state
```

## State rules

`useGraphStore.js` is the single source of truth. When you add anything that
accumulates:

1. Give it a module-level `MAX_*` cap next to `MAX_EXECUTION_LOGS`.
2. Trim on write, not on read.
3. Release it in `clearMemoryStore()` so the `Flush` button covers it.

## Styling

Tailwind 4 with tokens declared in `src/config/theme.js` and surfaced as
utilities through `tailwind.config.js`. **Do not hardcode hex colours in
components** — import from `@/config/theme` (or use a semantic class such as
`text-status-red`), so the obsidian palette stays in one place.

## Error handling

`ErrorBoundary` wraps the whole app in `src/app/layout.jsx`. A throw during
render degrades to a recovery card instead of a blank studio. Everything else
routes through `notify(message, tone)` in the store, which renders the toast
host at the bottom of `page.jsx`.

## Verifying a change

```bash
npm run lint
npm run build
```

Both must pass — CI runs exactly these two commands. See the repository
[CONTRIBUTING.md](../CONTRIBUTING.md) for the full workflow.
