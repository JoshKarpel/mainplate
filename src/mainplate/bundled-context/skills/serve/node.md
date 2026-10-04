# Node

## Anything started with `npm run`

Arguments after `--` reach the script's command: `npm run dev -- --port $PORT`.
Read the script in `package.json` first, since it decides which flags exist.

## Vite

```sh
npx vite --host 127.0.0.1 --port "$PORT" --strictPort
```

`--strictPort` fails rather than silently moving to the next port, which the
console would not know about. Hot module replacement is on by default.

Vite refuses requests whose `Host` it does not recognise. Allow any in the
config's `server` block, which has no command-line flag:

```js
server: { allowedHosts: true }
```

The replacement client connects back to the host and port the page was loaded
from, which is the console's link, so leave `server.hmr.port` and
`server.hmr.clientPort` unset: setting either to the port inside the sandbox
points the browser at a port it cannot reach.

## Next.js

```sh
npx next dev -H 127.0.0.1 -p "$PORT"
```

Reload is on by default. Next.js limits which origins may load its dev-only
resources, such as the reload connection, through `allowedDevOrigins` in
`next.config`. If the page loads but does not update, or the server logs a
blocked cross-origin request, add the host the person opens the link on.

## A server of your own

`node --watch server.js` restarts on changes to the files it imports (Node 18.11
and later). `nodemon --watch src --exec 'node server.js'` where the project has
nodemon. Read `process.env.PORT` and listen on `127.0.0.1`.
