# Systems lab — Local frontend + backend

## Start

From this repository root, run:

```sh
python3 backend/server.py
```

Open **http://127.0.0.1:8107**. Choose a tool and action, fill in the inputs, then run it. Export JSON saves actual returned results. Each repository has its own frontend and server; no combined cross-repository dashboard is required.

Use another port with `python3 backend/server.py --port 9000`. Python 3.10+ is required; no pip or npm installs are needed for this added UI. C compiler and snapshot actions also require GCC and GNU Make; snapshot tools require Linux. Binaries are built on first use.

## What is connected

Original bundled engines · Python Redis twin + heuristic agent · Compiler needs GCC + Make

- Browser: `frontend/index.html`, `styles.css`, `app.js`.
- Backend: `backend/server.py`; project-specific actions configured in `frontend/project.json`.
- `GET /api/config`, `GET /api/health`, `POST /api/run`.
- JSON POST body: `{"tool":"<tool id>","action":"<action id>","values":{}}`.
- Same-origin JSON requests must send `X-Project-Request: 1`.
- Persistent UI state lives in `.web-data/`, never in the original CLI data files. Override with `PROJECT_UI_DATA`.
- No automatic sample expenses or workouts are inserted. School activities retain the original educational sample registrations.

## Safety and limits

This is a **local educational UI**, not an authenticated public service. The server binds only to `127.0.0.1`, checks Host and Origin, rejects cross-origin writes, and never executes shell strings. It handles actions serially to protect the non-concurrent engines. Do not expose it through a proxy or public tunnel without authentication, CSRF protection suitable for that deployment, isolation, rate limiting and TLS. Submitted C is compiled only; produced assembly is not executed. Native educational tools are not a security sandbox and must only process trusted local inputs.

Existing files, websites, course workflows, CLI entry points and infrastructure are preserved. Static-site checkout/registration remain original demos; this package does not implement payments, real account registration or public deployment. Fitness is an explicitly labeled local prototype rather than completion of the course's Django/MongoDB stack. The agent UI deliberately uses the repository's MockLLM. The Redis UI uses the original Python twin in-process rather than connecting to Java/TCP. Offline search is a fixed catalog, not web-wide search.

## Tests

```sh
python3 -m unittest discover -s backend/tests -v
```

The new tests use temporary data. Original project tests remain available in their existing locations. For the local branch, review `git diff` and stage the additions before committing. No remote branch or pull request is created by this download.
