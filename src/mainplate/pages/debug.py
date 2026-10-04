# The debug page: what this process is holding, read at the moment the page was asked for.
#
# Not about any conversation, so it is drawn in a bar of its own as an artifact's page is, with no
# session list and no live connection: what it shows is a reading taken once, and a reload is how to
# take another. Each section is one thing the process keeps; the first, and so far the only one, is
# the memo every memoized function shares.

from __future__ import annotations

from typing import Final

from without_html import Element
from without_html import a
from without_html import article
from without_html import div
from without_html import dl
from without_html import h2
from without_html import p
from without_html import section
from without_html import table
from without_html import tbody
from without_html import td
from without_html import th
from without_html import thead
from without_html import tr

from mainplate.memo import Account
from mainplate.memo import Tally
from mainplate.pages.document import Links
from mainplate.pages.document import document
from mainplate.pages.document import fact
from mainplate.pages.document import pagebar
from mainplate.pages.figures import portion
from mainplate.pages.figures import sized

DEBUG: Final = "Debug"

MEMOIZED: Final = "Memoized"

# What each column of a function's row is, in the order they are drawn.
COLUMNS: Final = ("function", "hits", "misses", "hit rate", "entries", "held", "evicted")


def hit_rate(tally: Tally) -> str:
    """How often a call was answered from the memo, or a dash for a function nobody has called yet."""
    calls = tally.hits + tally.misses
    return "-" if not calls else portion(tally.hits / calls)


def tally_row(tally: Tally) -> Element:
    """
    One memoized function as a row: who it is, how it has been answered, and what it holds now.

    The name is the module and the function, because two modules may each memoize something called
    the same thing, and the name is what says where to look.
    """
    return tr(
        children=[
            th(attrs={"scope": "row"}, cls="debug__name", children=tally.name),
            td(children=f"{tally.hits:,}"),
            td(children=f"{tally.misses:,}"),
            td(children=hit_rate(tally)),
            td(children=f"{tally.entries:,}"),
            td(children=sized(tally.weight)),
            td(children=f"{tally.evicted:,}"),
        ]
    )


def memo_section(account: Account) -> Element:
    """
    The memo: what it may hold, what it holds, and every function holding it, heaviest first.

    Heaviest first, because the question this answers is whether the budget is the right size and,
    when it is not, who is spending it. A function memoized and never yet called is still drawn, with
    nothing against it, so the page says what is memoized rather than only what has been used.
    """
    tallies = sorted(account.tallies, key=lambda tally: (-tally.weight, tally.name))
    return section(
        cls="dashboard__places",
        attrs={"aria-label": MEMOIZED},
        children=[
            h2(cls="picker__legend", children=MEMOIZED),
            article(
                cls="dashboard__card",
                children=[
                    dl(
                        cls="facts",
                        children=[
                            *fact("held", sized(account.held), cls="debug__held"),
                            *fact("budget", sized(account.budget), title="Settings.memo_bytes"),
                            *fact("full", portion(account.held / account.budget) if account.budget else "-"),
                        ],
                    ),
                    div(
                        cls="debug__table",
                        children=table(
                            children=[
                                thead(
                                    children=tr(
                                        children=[th(attrs={"scope": "col"}, children=named) for named in COLUMNS]
                                    )
                                ),
                                tbody(children=[tally_row(tally) for tally in tallies]),
                            ]
                        ),
                    )
                    if tallies
                    else p(cls="dashboard__none", children="Nothing is memoized."),
                ],
            ),
        ],
    )


def debug_section(links: Links) -> Element:
    """
    The dashboard's way to the debug page, last, since it is about the console rather than its work.

    A link and nothing drawn from the reading itself: the dashboard is redrawn by the live connection
    and the reading is not, for the reason `debug_page` gives.
    """
    return section(
        cls="dashboard__places",
        attrs={"aria-label": "This console"},
        children=[
            h2(cls="picker__legend", children="This console"),
            article(
                cls="dashboard__card",
                children=div(
                    cls="dashboard__foot",
                    children=a(attrs={"href": links.to_debug()}, children="what this process is holding"),
                ),
            ),
        ],
    )


def debug_page(links: Links, account: Account) -> str:
    """
    What this process is holding, as one reading: so far, the memo.

    **Not live, deliberately.** A page whose figures moved under a reader would be drawing its own
    renders into the counts it shows, since every render goes through the memo; a reading taken once
    and retaken by a reload says exactly what was true at one moment and nothing about itself.
    """
    return document(
        links,
        DEBUG,
        div(
            cls="debug",
            children=[
                pagebar(
                    links,
                    DEBUG,
                    after=[a(cls="pagebar__link", attrs={"href": links.to_debug_json()}, children="as JSON")],
                ),
                div(cls="dashboard", children=[memo_section(account)]),
            ],
        ),
        live=False,
    )
