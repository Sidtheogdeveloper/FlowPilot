# Analyst interface

React/TypeScript client for the Phase 2 workflow API. Requires Node 22.12 or newer (tested with Node 24). Start the backend on `127.0.0.1:8000`, then run:

```
npm install
npm run dev
```

Open the Vite URL (normally `http://127.0.0.1:5173`). `/api` is proxied to the backend so no browser credentials or model tokens are stored in this client. Live Hugging Face configuration belongs to the backend.

`npm test` runs 18 checks covering progress, validation messages, request rejection, multipart uploads, exact submitted request preservation, independent graph layouts, and stale-response selection guards. `npm run build` performs a strict TypeScript check and generates the production bundle in `dist`. Build artifacts need a server that proxies `/api` to the backend; opening `index.html` directly is unsupported.

The sidebar lists PostgreSQL-backed chats and uploaded datasets, with search and a mobile navigation drawer. Each successful plan submission creates an independent analysis chat with its original request, dataset, planner, policy and DAG. Selecting a dataset creates a new draft that reuses that CSV; it does not modify an existing analysis. Saved chats can also be reopened directly with `?chat=<workflow-id>`. Drafts are held in memory while browsing chats and are not persisted across page reloads.

The conversation shows the actual validated plan and execution state, rather than invented assistant messages. The SVG follows each plan's dependencies, including parallel branches and joins. Explicit start/pause/resume/cancel controls, task progress and accepted artifact downloads stay visible. Execution details disclose workers, provenance, metrics and events. Model and scheduler choices are in the composer's Analysis settings menu. Template mode supports the approved retail workflow; Local Qwen requires the backend model service.

Failed validation preserves the draft. Generation and upload responses cannot replace a chat selected while a request was in flight, and detail polling ignores older responses after navigation. Collection refreshes update history without altering drafts. Backend integration and browser interaction verification remain separate from these frontend unit checks.


Completed analysis reports include structured insights derived by the backend from accepted report data: a headline, summary, findings and limitations. They are presented before downloads. A follow-up in a terminal chat creates and auto-starts a new validated DAG version on the same CSV; no upload or dataset switching is available in that composer. Supported examples include changing the IQR multiplier and asking for product revenue within a calendar month. Prior versions remain expandable with their individual requests, graphs, outputs and insight summaries. The version selector opens any saved version; only the latest terminal version accepts a follow-up. Follow-up drafts are held separately per stable chat id, and delayed responses cannot redirect a user who has navigated elsewhere. The unit suite additionally checks version ordering, graph isolation, latest-version authority and follow-up payload constraints.
