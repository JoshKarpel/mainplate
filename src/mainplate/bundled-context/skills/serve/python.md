# Python

Run through the project's own runner (`uv run`, `poetry run`, the virtualenv's
`bin/`), so the server sees the project's dependencies.

## Django

```sh
python manage.py runserver "127.0.0.1:$PORT"
```

It restarts on code changes by default. With `DEBUG` on and `ALLOWED_HOSTS`
empty, Django accepts only `localhost` and loopback addresses, so the
console's link is refused with a 400: set `ALLOWED_HOSTS = ["*"]` in the
development settings only.

Behind the console's HTTPS link a form `POST` can fail its CSRF origin check,
since the browser's origin is `https://` and the host the console serves on,
which Django did not see. Add that origin to `CSRF_TRUSTED_ORIGINS`, again in
development settings only.

## Flask

```sh
flask --app myapp run --debug --host 127.0.0.1 --port "$PORT"
```

`--debug` turns on the reloader.

## FastAPI, Starlette, or anything on uvicorn

```sh
uvicorn myapp:app --reload --reload-dir src --host 127.0.0.1 --port "$PORT"
```

`--reload-dir` keeps it to the sources, which matters when the project writes
files under its own directory. `fastapi dev` reloads by default and takes the
same `--host` and `--port`. An app using `TrustedHostMiddleware` needs `*`
among its allowed hosts in development.

## MkDocs

```sh
mkdocs serve -a "127.0.0.1:$PORT"
```

It rebuilds and reloads the page on changes to the docs and `mkdocs.yml`.

## Static files

`python -m http.server "$PORT" --bind 127.0.0.1` serves the directory as it is
at each request, so a refresh shows an edit and no watcher is needed.

## A process that reads its configuration once

Wrap it in `watchfiles`, naming the directories that hold the sources and the
configuration:

```sh
exec watchfiles 'python -m myapp' src config
```

Its default filter already skips `.git`, `__pycache__`, `node_modules` and the
like. `--filter python` narrows it to `.py` files, which misses a changed
template or configuration file.
