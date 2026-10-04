---
description: Run a dev server as a job the person opens in a browser, and restart it when the files it serves change
---
# Serving something the person opens

A server runs as a **job**: `start_job` with the command and the `port` it
listens on. `start_job`'s own description is the rules, and they hold here too:
listen on `127.0.0.1` at `$PORT`, accept any `Host`, use relative URLs, make the
command safe to start twice, and remember that your `bash` cannot reach the job.
This page is how to write a command that satisfies them, and keeps serving the
current files while you edit.

## Write the command as a script once it is more than one line

A job's command is run with `sh -c`. Past a line or two, put it in a script in
the repository (or under `$MAINPLATE_SCRATCH` if it is only for this session)
and start the script. `exec` the server as the script's last line, so the
process the console stops, or a watcher restarts, is the server itself rather
than a shell that leaves the server holding the port.

`generated/demo` beside this file is a real one: the job this console's own
repository runs for every session working on it. It writes its configuration
into the scratch, starts a helper in the background, seeds fixtures in a way
that is safe to repeat, and `exec`s the server under a watcher.

## Restarting on change

Use the first of these that the session already has. A session may have no
network, so a watcher that has to be fetched first (`npx`, `uvx`,
`cargo install`, `go install`) may not be available; check with `command -v`
before relying on one.

### 1. The framework's own reload

Most dev servers already watch their sources: Vite and Next.js replace modules
in the page, and `uvicorn --reload`, `flask run --debug`, Django's `runserver`,
Rails and Phoenix reload code. Nothing more is needed. The per-ecosystem files
below say which flag turns it on.

Reach past it for a server with no reload, or one that reads something once at
startup that its own reload does not cover, such as a template or asset
inventory, or a configuration file.

### 2. The ecosystem's watcher

What a project in that language most likely already depends on:

| Ecosystem | Restarts a command on change |
|---|---|
| Python | `watchfiles 'python -m myapp' src` |
| Node | `node --watch server.js` (follows what it imports), `nodemon --watch src --exec 'node server.js'`, `tsx watch src/server.ts` |
| Rust | `cargo watch -x run` |
| Go | `air`, configured by `.air.toml` |
| Ruby | `rerun 'ruby app.rb'` |
| JVM | `./gradlew -t build` rebuilds on change; pair it with the framework's own restart (Spring Boot DevTools) |

### 3. A generic watcher

These work for any command in any language:

- **`watchexec`** is the most capable:
  `watchexec -r -w src -e py,html -- python -m myapp`. `-r` restarts rather
  than queueing, `-w` names what to watch, `-e` filters by extension, and it
  honours `.gitignore` by default.
- **`entr`** is small and often packaged by the distribution. It watches a list
  of files read on stdin, fixed when it starts; `-d` makes it exit when a file
  is added to a watched directory, so loop it to pick up new files:
  `while true; do git ls-files | entr -dr python -m myapp; done`.
- **`inotifywait`**, from inotify-tools, only reports events; a loop around it
  has to stop and start the server itself, which is what the other two already
  do. Prefer them where they exist.

### 4. No watcher at all

`restart-on-change` beside this file needs only `sh`, `git` and coreutils. It
polls once a second and restarts the command when any file git would show
changes, skipping what `.gitignore` names. Read it, write a copy into the
repository or the scratch, and start that:
`sh "$MAINPLATE_SCRATCH/restart-on-change" python -m myapp`. Polling costs a
`stat` of every file each second, so prefer a real watcher in a large
repository.

### Whichever you use

- **Watch the sources, never what the server writes.** A build directory, a
  log, `.git`, `node_modules` or `__pycache__` in the watched set restarts the
  server on its own output, for ever. Name the source directories explicitly
  rather than watching `.`.
- **A restart has to free the port.** The watcher must stop the old process
  before starting the new one, which `-r` and its equivalents do; a loop of
  your own has to `kill` the old process and `wait` for it.
- **If changes are not noticed**, switch the watcher to polling:
  `WATCHFILES_FORCE_POLLING=true`, `watchexec --poll`, `CHOKIDAR_USEPOLLING=1`
  for tools built on chokidar, `server.watch.usePolling` in Vite.

A watcher is safe to start twice in the sense a job needs: started again from
the top after a console restart, it serves the files as they are then.

## Before starting

- `list_jobs` first. Starting the same command twice starts two jobs, and the
  second fails to bind the port. `stop_job` the one you are replacing.
- To check the server answers, run it and `curl` it **inside one `bash`
  command**, then stop it, before starting the job. Poll rather than sleeping
  a guessed amount:

  ```sh
  python -m myapp & pid=$!
  for _ in $(seq 40); do curl -fsS 127.0.0.1:8000/ >/dev/null && break; sleep 0.25; done
  curl -fsS 127.0.0.1:8000/ | head; kill $pid
  ```
- Confirm a flag with the tool's own `--help` before relying on it: these move
  between versions, and the installed version is the one that matters.
- A server every session on this repository wants is better declared by the
  repository's setup plugin than started by hand each time; see the `setup`
  skill.

## Per ecosystem

Read only the one you need:

- `node.md`: Vite, Next.js, and anything started with `npm run`.
- `python.md`: Django, Flask, FastAPI or anything on uvicorn, MkDocs.
- `ruby.md`: Rails.
