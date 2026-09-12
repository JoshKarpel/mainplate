# Screenshot the gallery, so a styling change can be looked at rather than asserted about.
#
# Playwright drives a real Chromium over a real static server, which is what makes the shot worth
# reading: the stylesheet, the script, and htmx are the ones the console ships, loaded the way the
# console loads them. What it is not is a test. Nothing here asserts; it produces PNGs for a person
# (or an agent that can read one) to look at.
#
# The same Playwright the suite drives, so a checkout pins one Chromium and `just dependencies`
# fetches it once. The sync binding rather than the suite's async one, because this runs on its own
# with no event loop to collide with; `tests/AGENTS.md` says why the suite cannot make that choice.
#
# Usage: python -m scripts.shoot <gallery-dir> <out-dir> [page.html[#anchor] ...]

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Final

from playwright.sync_api import ConsoleMessage
from playwright.sync_api import Page
from playwright.sync_api import Request
from playwright.sync_api import Response
from playwright.sync_api import ViewportSize
from playwright.sync_api import sync_playwright

from scripts.gallery import pages

# Wide enough that the rail stands beside the conversation rather than sliding off, and tall
# enough that a whole turn fits in one shot. The phone viewport is the other case worth seeing,
# because the rail's breakpoint is the thing most likely to be wrong.
VIEWPORTS: Final[dict[str, ViewportSize]] = {
    "wide": ViewportSize(width=1400, height=1600),
    "phone": ViewportSize(width=390, height=844),
}

# The console's script pins the theme and reapplies the reader's projections after load, so a shot
# taken before it settles is of a page nobody sees.
SETTLING_MS: Final = 150


@dataclass(frozen=True, slots=True)
class Target:
    """One page to shoot, which is a file in the gallery and maybe an anchor to land on in it."""

    file: str
    fragment: str | None

    @classmethod
    def parse(cls, asked: str) -> Target:
        file, _, fragment = asked.partition("#")
        return cls(file=file, fragment=fragment or None)

    @property
    def name(self) -> str:
        stem = self.file.removesuffix(".html")
        return f"{stem}-{self.fragment}" if self.fragment else stem

    @property
    def path(self) -> str:
        return f"{self.file}#{self.fragment}" if self.fragment else self.file


def diagnosing(page: Page, label: str) -> None:
    """
    Print what a screenshot cannot show, to stderr, beside the shot it is about.

    Surfacing these is the whole reason to drive a real browser: a stylesheet that 404s or a script
    that throws is invisible in a screenshot and obvious here. Nothing fails on any of them, since
    this is a diagnostic for somebody already looking; `tests/test_browser.py` is where the same
    questions fail a build.
    """

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error":
            print(f"  [{label}] console: {message.text}", file=sys.stderr)

    def on_request_failed(request: Request) -> None:
        print(f"  [{label}] failed: {request.url} {request.failure or ''}", file=sys.stderr)

    def on_response(response: Response) -> None:
        if response.status >= 400:
            print(f"  [{label}] {response.status} {response.url}", file=sys.stderr)

    page.on("console", on_console)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def shoot(gallery: Path, out: Path, targets: tuple[Target, ...]) -> tuple[Path, ...]:
    """
    Every target at every viewport, as the files written.

    Port zero, so a shoot taken while a console is running beside it needs nothing stopped and
    fights nobody for a number: the kernel hands one over and the browser is told which.
    """
    out.mkdir(parents=True, exist_ok=True)
    serving = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(gallery)))
    thread = Thread(target=serving.serve_forever, daemon=True)
    thread.start()
    shots: list[Path] = []
    try:
        base = f"http://127.0.0.1:{serving.server_address[1]}"
        with sync_playwright() as driving:
            browser = driving.chromium.launch()
            try:
                for label, viewport in VIEWPORTS.items():
                    page = browser.new_page(viewport=viewport)
                    diagnosing(page, label)
                    for target in targets:
                        page.goto(f"{base}/{target.path}", wait_until="load")
                        page.wait_for_timeout(SETTLING_MS)
                        # The property that must hold on every page at every width: the transcript
                        # may scroll its own wide blocks, but the document must never scroll sideways.
                        room = page.evaluate(
                            "() => ({ document: document.documentElement.scrollWidth,"
                            " viewport: document.documentElement.clientWidth })"
                        )
                        if room["document"] > room["viewport"]:
                            print(
                                f"  [{label}] {target.name}: HORIZONTAL OVERFLOW"
                                f" (document {room['document']}px > viewport {room['viewport']}px)",
                                file=sys.stderr,
                            )
                        path = out / f"{target.name}-{label}.png"
                        page.screenshot(path=str(path), full_page=label == "wide")
                        shots.append(path)
                    page.close()
            finally:
                browser.close()
    finally:
        serving.shutdown()
        thread.join()
        serving.server_close()
    return tuple(shots)


def main(argv: list[str]) -> None:
    gallery, out, *asked = argv
    # Asked of `pages()` rather than listed here, so a page added to the gallery is shot without this
    # file being told; the render nobody looks at is pure and costs only itself.
    targets = tuple(Target.parse(each) for each in (asked or sorted(pages())))
    for shot in shoot(Path(gallery), Path(out), targets):
        print(shot)


if __name__ == "__main__":
    main(sys.argv[1:])
