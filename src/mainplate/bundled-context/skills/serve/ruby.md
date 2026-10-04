# Ruby

## Rails

```sh
bin/rails server -b 127.0.0.1 -p "$PORT"
```

Code reloads on each request in development. Rails' host authorization refuses
an unrecognised `Host` in development, so the console's link shows a "Blocked
hosts" page: allow it in `config/environments/development.rb`, with
`config.hosts.clear` to allow any.

A server holding a stale PID file from a previous start refuses to boot.
Removing `tmp/pids/server.pid` before `exec`ing the server is what makes the
command safe to run twice.
