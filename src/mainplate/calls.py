# What one tool call is drawn as: the line a folded call shows beside the tool's name, and the body
# under it once a reader opens the fold.
#
# The body is drawn per tool where this console knows the tool, and generically where it does not.
# A `bash` call is a shell command with its output under it; an `edit` is the diff of what it
# changed; a `read`, a `create` and a `grep` are lines of a file, coloured by the file's own
# grammar, with the anchors the tool drew in front of them left out. Everything else, a plugin's
# tool included, is its arguments laid out one to a row and its return verbatim, which is what
# every call was drawn as before any of the tools had a rendering of its own.
#
# **What the model was handed is on the page, or a press away.** Nothing here shows the model's
# arguments or return as anything other than what they were, with two exceptions that are both
# stated on the design page: an `edit` with a recorded diff shows the diff in place of its
# operations and its reply, and a file's lines are shown without the anchors the tool wrote in
# front of them, which are the model's names for lines and mean nothing to a person. The raw record
# hangs off the request either way.

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Final
from typing import Literal

from markupsafe import Markup
from markupsafe import escape
from without_html import Element
from without_html import Node
from without_html import code
from without_html import dd
from without_html import div
from without_html import dl
from without_html import dt
from without_html import pre
from without_html import span

from mainplate.conversation import ToolUse
from mainplate.markup import SHELL
from mainplate.markup import highlighted
from mainplate.markup import language_of
from mainplate.markup import linked_text
from mainplate.tools.files.anchors import ALPHABET
from mainplate.tools.files.anchors import GUTTER
from mainplate.tools.files.anchors import UNADDRESSABLE
from mainplate.tools.files.anchors import WIDTH
from mainplate.tools.files.tools import DIFF

# How wide JSON is indented where a person reads it. Two, because the point of showing it is the
# shape, and a value nested four deep at four spaces is mostly margin in a column this narrow.
INDENT: Final = 2

# The two labels a call's body is read under, written here and read by `mainplate.js`, which joins a
# copied call back together from the list the page draws it as and names neither.
CALLED_WITH: Final = "called with"
RETURNED: Final = "returned"

# The tools whose calls are drawn open. A `create` is, because the new file is what a reader watching
# a turn is watching for; every other call is drawn shut, since what a read brought back or a command
# said is context a reader reaches for, and a turn of twenty reads drawn open is twenty screens of
# file. An `edit` used to be open too, for the diff; that diff now stands at the block level below the
# panel, covering the whole batch, so the per-edit diff is a press away. Per tool and never per state,
# which is the one rule the fold script rests on: a fold whose default moved as its result landed
# would be recorded as a decision nobody made; see `wireFolds` in `mainplate.js`.
WRITING: Final = frozenset({"create"})

# The tools whose returns carry lines of a file behind an anchor, which is what `rows_of` reads. The
# console's own and nobody else's: a plugin's tool could print the same shape and would be shown it
# verbatim, since nothing says its `path` names a file.
ANCHORING: Final = frozenset({"read", "create", "edit", "grep"})

# What the tool drew in front of a line of a file, as `Anchored.rendered` draws it: four letters or
# the marker for a line that cannot be named, then the bar. Built from the scheme's own constants
# rather than written out, so a change to the scheme is a change here too.
ANCHORED: Final = re.compile(rf"^(?P<anchor>[{ALPHABET}]{{{WIDTH}}}|{re.escape(UNADDRESSABLE)}){GUTTER}")

# A unified diff's hunk header, which is where the line numbers on either side come from. The count
# after the comma is left off for a hunk of one line, which is the format and not a shortcut.
HUNK: Final = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")


def starts_open(tool: str) -> bool:
    """Whether a call to this tool is drawn with its fold open; see `WRITING`."""
    return tool in WRITING


def laid_out(said: str) -> str:
    """
    JSON laid out to be read, and anything else left exactly as it arrived.

    What a model hands a tool is JSON by construction, and a single line of it is where a reader
    has to count brackets to find the argument they came for. It is not *reliably* well-formed,
    though: `args_as_json_str` returns whatever the provider sent when the arguments arrived as a
    string, so a malformed call reaches here as that text. Showing it unchanged is the honest
    rendering, and it is the call a reader most needs to look at.

    Deliberately not used on what a call *returned*. That is whatever the tool produced - usually
    the contents of a file - so text that merely happens to parse as JSON would be reformatted, and
    a reader would be shown something other than what the model was handed.
    """
    try:
        return json.dumps(json.loads(said), indent=INDENT, ensure_ascii=False)
    except ValueError:
        return said


def arguments_of(arguments: str) -> dict[str, object] | None:
    """
    What a model handed a tool, as the object it is, or nothing where it is not one.

    Nothing covers both a call that is not JSON and one that is JSON of some other shape, because
    both are drawn the same way: as the text that arrived, through `laid_out`, which is the call a
    reader most needs to see as it was.
    """
    try:
        handed = json.loads(arguments)
    except ValueError:
        return None
    return handed if isinstance(handed, dict) else None


@dataclass(frozen=True, slots=True)
class Subject:
    """
    What one call was about, as the words a folded call shows beside the tool's name.

    `said` is the thing the call acted on - a path, a command - and `extent` is how much of it, where
    the tool has a way of saying less than all: which lines of a file, how many operations, how deep
    a listing went. Two slots rather than one sentence so the stylesheet can let the first give way
    to an ellipsis while the second stays whole.
    """

    said: str
    extent: str | None = None


def lines_of(offset: object, limit: object) -> str | None:
    """
    Which lines a `read` asked for, or nothing where it asked for all of them.

    Both figures are taken as the model sent them, which is JSON and so could be anything: a figure
    that is not an integer is treated as not given rather than refused, because what is being drawn
    is a summary of a call and the call itself is a press away, exactly as it arrived.
    """
    from_line = offset if isinstance(offset, int) and not isinstance(offset, bool) else None
    at_most = limit if isinstance(limit, int) and not isinstance(limit, bool) else None
    if from_line is not None and at_most is not None:
        return f"lines {from_line}\N{EN DASH}{from_line + at_most - 1}"
    if from_line is not None:
        return f"from line {from_line}"
    if at_most is not None:
        return f"first {at_most} lines"
    return None


def subject_of(tool: str, arguments: str) -> Subject | None:
    """
    What a call to one of this console's own tools was about, read off what the model handed it.

    Named per tool rather than guessed from whichever field looks like a subject, because the four
    file tools and `bash` are the whole of what this console defines and each has one field that is
    the point of the call. A plugin's tool is not here: its arguments are its own vocabulary and
    nothing here can say which of them is the subject, so a call to one is named and nothing more.

    Nothing that is not a well-formed object with the expected field in it produces a subject. A
    malformed call is the one a reader most needs to open, and `laid_out` shows it unchanged in the
    body; a summary that guessed at it would be a second rendering of the thing that went wrong.
    """
    handed = arguments_of(arguments)
    if handed is None:
        return None
    if tool == "bash":
        command = handed.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        first, _, rest = command.strip().partition("\n")
        more = rest.count("\n") + 1 if rest else 0
        return Subject(first, extent=f"{more} more line{'s' if more != 1 else ''}" if more else None)
    if tool not in ("read", "edit", "create", "list"):
        return None
    path = handed.get("path", "." if tool == "list" else None)
    if not isinstance(path, str) or not path:
        return None
    root = handed.get("root")
    said = f"{root}:{path}" if isinstance(root, str) and root else path
    if tool == "read":
        return Subject(said, extent=lines_of(handed.get("offset"), handed.get("limit")))
    if tool == "edit":
        operations = handed.get("operations")
        if not isinstance(operations, list):
            return Subject(said)
        return Subject(said, extent=f"{len(operations)} operation{'s' if len(operations) != 1 else ''}")
    if tool == "list":
        depth = handed.get("depth")
        deep = depth if isinstance(depth, int) and not isinstance(depth, bool) else None
        return Subject(said, extent=f"depth {deep}" if deep is not None else None)
    return Subject(said)


# --- Lines behind a gutter -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Row:
    """
    One line of what a tool returned, split where the tool stops talking and the file starts.

    `anchor` is what stood before the bar - a name, or the marker for a line that has none - and is
    absent on a line the tool wrote itself: the header saying which file and which lines, the note
    saying an anchor moved. `text` is the rest, which on a file's line is the line as it is in the
    file.
    """

    anchor: str | None
    text: str

    @property
    def is_file(self) -> bool:
        return self.anchor is not None


def rows_of(content: str) -> tuple[Row, ...]:
    """Every line of a return, each split at its gutter where it has one."""
    rows = []
    for line in content.split("\n"):
        found = ANCHORED.match(line)
        rows.append(
            Row(anchor=None, text=line) if found is None else Row(anchor=found["anchor"], text=line[found.end() :])
        )
    return tuple(rows)


def line_element(gutter: str | None, marked: Markup | str, mark: str | None = None, *, said: bool = False) -> Element:
    """
    One line of a block, with what the console draws in front of it carried as data.

    The gutter is an attribute and not text, and the stylesheet paints it, which is what makes
    copying a diff copy the diff: the line numbers are the console's and are not in the change. The
    newline is inside the line rather than between lines, so a line can be a block that paints its
    whole row while the text of the block still reads as lines. `said` marks a line the tool wrote
    itself rather than one of the file's, so the stylesheet can set the two apart in tone.
    """
    return span(
        cls="line", attrs={"data-gutter": gutter, "data-mark": mark, "data-said": said}, children=[marked, "\n"]
    )


def anchored_element(content: str, language: str | None) -> Element:
    """
    What `read`, `create`, `edit` and `grep` return: the tool's own lines, and the file's without
    the anchors the tool drew in front of them.

    The anchors are left out rather than drawn faint, because they are the model's names for lines
    and say nothing to a person: what tells a file's line from the tool's is the tone the stylesheet
    sets a tool's own line in, and what tells one line from the next is the file. A reader working
    out which anchor the model meant has the raw record on the request.

    Each run of the file's lines is coloured as one text rather than a line at a time, so a string
    or a comment that spans lines is read as one token. A run is what lies between two lines the
    tool wrote, which for a read is the whole file and for a search is each region, so a token
    spanning two regions is cut where the regions are, which is also where the model's reading of
    it is cut.
    """
    lines: list[Element] = []
    for is_file, run in groupby(rows_of(content), key=lambda row: row.is_file):
        rows = tuple(run)
        marked: tuple[Markup, ...]
        if is_file and language is not None:
            marked = highlighted(language, "\n".join(row.text for row in rows))
        else:
            marked = tuple(escape(row.text) for row in rows)
        lines.extend(line_element(None, markup, said=not is_file) for markup in marked)
    return pre(cls="lines", children=code(children=lines))


# --- A diff ---------------------------------------------------------------------------------------


type Mark = Literal["-", "+", " ", "@"]


@dataclass(frozen=True, slots=True)
class Change:
    """
    One line of a unified diff, with the line numbers it has on each side.

    A hunk header is a line too, marked `@`, with no numbers of its own: it is where the numbers of
    the lines under it are read from.
    """

    mark: Mark
    old: int | None
    new: int | None
    text: str


def changes_of(diff: str) -> Iterator[Change]:
    """
    A unified diff as lines, numbered on the side each one is on.

    The file headers are passed over: they name the path twice, and the fold's own summary already
    names it once. A line that is none of the shapes a unified diff has is refused, because the only
    thing that writes one of these is `diffed`, and a fixture that wrote one wrong should say so.
    """
    old = new = 0
    for line in diff.split("\n"):
        if line.startswith(("--- ", "+++ ")):
            continue
        if (header := HUNK.match(line)) is not None:
            old, new = int(header["old"]), int(header["new"])
            yield Change("@", None, None, line)
            continue
        match line[:1]:
            case "-":
                yield Change("-", old, None, line[1:])
                old += 1
            case "+":
                yield Change("+", None, new, line[1:])
                new += 1
            case " ":
                yield Change(" ", old, new, line[1:])
                old += 1
                new += 1
            case _:
                raise ValueError(f"not a line of a unified diff: {line!r}")


def gutter_width(changes: Iterable[Change]) -> int:
    """How wide each side of a diff's gutter has to be, from the widest line number it holds."""
    return max(
        (len(str(at)) for change in changes for at in (change.old, change.new) if at is not None),
        default=0,
    )


def diff_lines(changes: Sequence[Change], width: int) -> list[Element]:
    """One run of a diff as lines, numbered on each side, which `diff_element` and a block's files share."""
    lines = []
    for change in changes:
        if change.mark == "@":
            lines.append(line_element(" " * (2 * width + 2), change.text, "@"))
            continue
        old = "" if change.old is None else str(change.old)
        new = "" if change.new is None else str(change.new)
        lines.append(line_element(f"{old:>{width}} {new:>{width}} ", f"{change.mark}{change.text}", change.mark))
    return lines


def diff_element(diff: str) -> Element:
    """
    The change an `edit` made, as the diff the tool recorded beside its reply.

    The numbers on each side are the gutter, carried as data and painted by the stylesheet for the
    reason a read's anchors are: copied, the block is a diff with its marks and without the numbers
    the console drew. The mark is text, since a diff without its `-` and `+` is not one.
    """
    if not diff:
        return span(cls="tool__silent", children="no change")
    changes = tuple(changes_of(diff))
    return pre(cls=("lines", "diff"), children=code(children=diff_lines(changes, gutter_width(changes))))


def changes_by_file(diff: str) -> tuple[tuple[str, tuple[Change, ...]], ...]:
    """
    Each file a git diff changed, as its path and the numbered lines of its hunks.

    What git prints around the lines a reader wants - the `diff --git` header then the `index`,
    mode, rename and `---`/`+++` lines before the first hunk - is dropped, so what reaches
    `changes_of` is the same clean shape `diffed` produces. The path comes off the `diff --git`
    header, where it is the same on both sides whether the file was added, removed or edited. A
    binary change has no lines to show and is left out, which is the one reading `--no-renames`
    cannot turn into a line diff.
    """
    files: list[tuple[str, tuple[Change, ...]]] = []
    path: str | None = None
    hunks: list[str] = []
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            if path is not None and hunks:
                files.append((path, tuple(changes_of("\n".join(hunks)))))
            _, _, rest = line.partition(" a/")
            path = rest.partition(" b/")[0]
            hunks = []
        elif not line or line.startswith(
            (
                "index ",
                "new file mode",
                "deleted file mode",
                "old mode",
                "new mode",
                "similarity index",
                "dissimilarity index",
                "rename from",
                "rename to",
                "copy from",
                "copy to",
                "Binary files",
                "GIT binary patch",
                "--- ",
                "+++ ",
                "\\",
            )
        ):
            continue
        else:
            hunks.append(line)
    if path is not None and hunks:
        files.append((path, tuple(changes_of("\n".join(hunks)))))
    return tuple(files)


def block_diff_element(diff: str) -> Element:
    """
    The net change one batch of tool calls made, per file, with each file's path over its hunks.

    Drawn below the panel's calls rather than inside any one of them, because a batch may run an
    `edit`, a `create` and a `bash` at once and a diff for one call would say part of the change. The
    path is a line the console writes, marked `said`, and the hunks under it are the same numbered
    lines `diff_element` draws, one gutter width across the whole block so the columns line up.
    """
    files = changes_by_file(diff)
    if not files:
        return span(cls="tool__silent", children="no change")
    width = gutter_width(change for _, changes in files for change in changes)
    lines: list[Element] = []
    for path, changes in files:
        lines.append(line_element(None, path, said=True))
        lines.extend(diff_lines(changes, width))
    return pre(cls=("lines", "diff", "tool__batch"), children=code(children=lines))


def recorded_diff(used: ToolUse) -> str | None:
    """The diff an `edit` recorded beside its reply, or nothing where the call is not one that did."""
    if used.tool != "edit" or used.returned is None or not isinstance(used.returned.metadata, Mapping):
        return None
    found = used.returned.metadata.get(DIFF)
    return found if isinstance(found, str) else None


# --- The body ------------------------------------------------------------------------------------


def block_element(marked: tuple[Markup, ...]) -> Element:
    """A value that runs to lines, each on its own, with nothing in front of any of them."""
    return pre(cls="lines", children=code(children=[line_element(None, line) for line in marked]))


def value_element(tool: str, handed: Mapping[str, object], name: str, value: object) -> Element:
    """
    One argument as a reader sees it, which depends on what it is and, twice, on which tool got it.

    A shell command is coloured as one, and what `create` was handed to write is coloured by the
    grammar the path names, because those two are the arguments a reader actually reads. Any other
    string is shown as it is, on one line or several; anything that is not a string is JSON, laid
    out where it has a shape and inline where it is a bare value.
    """
    if isinstance(value, str):
        if tool == "bash" and name == "command":
            return block_element(highlighted(SHELL, value))
        if tool == "create" and name == "content":
            path = handed.get("path")
            language = language_of(path) if isinstance(path, str) else None
            return block_element(highlighted(language, value) if language else tuple(map(escape, value.split("\n"))))
        if "\n" in value:
            return block_element(tuple(map(escape, value.split("\n"))))
        return code(children=value)
    if isinstance(value, list | dict):
        return pre(children=code(children=json.dumps(value, indent=INDENT, ensure_ascii=False)))
    return code(children=json.dumps(value, ensure_ascii=False))


def unlabelled(tool: str, name: str) -> bool:
    """
    Whether an argument is drawn without its name in front of it, which is `bash`'s command alone.

    A coloured shell line under `called with` says what it is by its shape, and the fold's own
    summary is already that line, so a label would be the word `command` written above a command.
    Every other argument keeps its name, since a bare `300` or a bare path says nothing about which
    argument it was.
    """
    return tool == "bash" and name == "command"


def arguments_element(tool: str, arguments: str) -> Node:
    """
    What the model handed the tool, one argument to a row where the call is an object and verbatim
    where it is not.
    """
    handed = arguments_of(arguments)
    if handed is None:
        return pre(children=code(children=laid_out(arguments)))
    return [
        div(
            cls="argument",
            children=value_element(tool, handed, name, value)
            if unlabelled(tool, name)
            else [span(cls="argument__name", children=name), " ", value_element(tool, handed, name, value)],
        )
        for name, value in handed.items()
    ]


def returned_element(tool: str, arguments: str, content: str) -> Node:
    """
    What the tool gave back, which is the file's own lines for the tools that return them and the
    text as it was for everything else.

    A read and a create are coloured by the grammar the path they were handed names. A search is
    not: its regions come from as many files as matched, and which grammar each is in is in a header
    line this console would have to parse a second time to learn. That is the cost, stated.
    """
    if tool not in ANCHORING:
        return pre(children=code(children=linked_text(content)))
    handed = arguments_of(arguments)
    path = None if handed is None else handed.get("path")
    return anchored_element(content, language_of(path) if isinstance(path, str) and tool != "grep" else None)


def created(used: ToolUse) -> bool:
    """
    Whether this is a `create` that wrote its file, whose reply is then the whole of what to show.

    The reply is the new file under a line saying so, which is the content the call was handed with
    the tool's confirmation on top, so drawing the arguments as well would be the file twice. A
    create that was refused keeps its arguments, since the content it was handed is then nowhere
    else on the page, and one still out has no reply to stand in for them yet.
    """
    return used.tool == "create" and used.returned is not None and used.returned.outcome == "success"


def call_body(used: ToolUse) -> Element:
    """
    What a call was handed and what it gave back, under the two labels the script reads them by.

    Two calls are drawn as one half alone, because for each the other half would be the same thing
    again. An `edit` that recorded a diff is that: its operations are the diff said in anchors, and
    its reply is the diff's right-hand side said in anchors again, so either beside it would be the
    same change a third time. A `create` that succeeded is its reply: the new file under the tool's
    own line saying it was written, which is the content it was handed with a confirmation on top.
    A reader who wants the anchors an edit was addressed by has the raw record on the request.
    """
    if (diff := recorded_diff(used)) is not None:
        return dl(cls="tool__body", children=[dt(children=DIFF), dd(children=diff_element(diff))])
    if created(used) and used.returned is not None:
        return dl(
            cls="tool__body",
            children=[
                dt(children=RETURNED),
                dd(children=returned_element(used.tool, used.arguments, used.returned.content)),
            ],
        )
    return dl(
        cls="tool__body",
        children=[
            dt(children=CALLED_WITH),
            dd(children=arguments_element(used.tool, used.arguments)),
            *(
                ()
                if used.returned is None
                else (
                    dt(children=RETURNED),
                    dd(children=returned_element(used.tool, used.arguments, used.returned.content)),
                )
            ),
        ],
    )
