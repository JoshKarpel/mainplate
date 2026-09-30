from __future__ import annotations

import json

import pytest
from without_html import render

from mainplate.calls import Change
from mainplate.calls import Row
from mainplate.calls import block_diff_element
from mainplate.calls import call_body
from mainplate.calls import changes_by_file
from mainplate.calls import changes_of
from mainplate.calls import rows_of
from mainplate.conversation import Returned
from mainplate.conversation import ToolUse
from mainplate.pages import LONGEST_OPEN_DIFF
from mainplate.pages import batch_element
from mainplate.pages import tool_block
from mainplate.tools.files.tools import diffed

PYTHON_READ = "a.py, 3 lines\n\nqwrt│def f():\n----│\nmkpv│    return 1"

# One literal, because a context line that is blank in the file is a single space in the diff and
# a trailing space is what every editor strips off the end of a line.
DIFF = "--- a.py\n+++ a.py\n@@ -1,3 +1,3 @@\n def f():\n-    return 1\n+    return 42\n "


def drawn(tool: str, arguments: dict[str, object], returned: Returned | None) -> str:
    return render(call_body(ToolUse(tool=tool, arguments=json.dumps(arguments), returned=returned)))


def ran_a_pipe() -> str:
    """A `bash` call whose command has a token in it that a shell grammar colours."""
    return drawn("bash", {"command": "ls | wc -l"}, Returned(outcome="success", content="$ ls | wc -l\n\n3\n\nexit 0"))


def read_a_python_file() -> str:
    """A `read` of a file the path names a grammar for, with a blank line and the tool's own line in it."""
    return drawn("read", {"path": "a.py"}, Returned("success", PYTHON_READ))


def test_artifact_import_returns_a_versioned_link() -> None:
    """The return keeps its exact words and offers the immutable URL as a local link."""
    url = "/artifacts/abc_12?version=3"
    html = drawn("file_to_artifact", {"path": "page.html"}, Returned("success", f"Title: abc_12 version 3 {url}"))
    assert f'href="{url}"' in html
    assert "Title: abc_12 version 3" in html


class TestReadingLinesBehindAGutter:
    """The tool's own lines are told from the file's by what stands in front of them."""

    def test_a_line_the_tool_drew_an_anchor_in_front_of_is_the_files(self) -> None:
        assert rows_of("qwrt│def f():") == (Row(anchor="qwrt", text="def f():"),)

    def test_a_line_with_no_anchor_is_still_the_files(self) -> None:
        assert rows_of("----│    pass") == (Row(anchor="----", text="    pass"),)

    def test_a_blank_line_of_the_file_keeps_its_gutter_and_has_no_text(self) -> None:
        assert rows_of("----│") == (Row(anchor="----", text=""),)

    def test_a_line_the_tool_wrote_itself_has_no_gutter(self) -> None:
        assert rows_of("a.py, 3 lines") == (Row(anchor=None, text="a.py, 3 lines"),)

    @pytest.mark.parametrize("line", ["ABCD│x", "abc│x", "abcde│x", "ab1d│x", " qwrt│x", "x qwrt│y"])
    def test_only_the_schemes_own_shape_at_the_front_of_a_line_is_a_gutter(self, line: str) -> None:
        assert rows_of(line) == (Row(anchor=None, text=line),)

    def test_a_bar_later_in_a_line_stays_in_the_line(self) -> None:
        assert rows_of("qwrt│a │ b") == (Row(anchor="qwrt", text="a │ b"),)


class TestReadingADiff:
    def test_each_line_is_numbered_on_the_side_it_is_on(self) -> None:
        assert list(changes_of(DIFF)) == [
            Change("@", None, None, "@@ -1,3 +1,3 @@"),
            Change(" ", 1, 1, "def f():"),
            Change("-", 2, None, "    return 1"),
            Change("+", None, 2, "    return 42"),
            Change(" ", 3, 3, ""),
        ]

    def test_a_second_hunk_restarts_the_numbers_where_its_header_says(self) -> None:
        found = list(changes_of("@@ -1 +1 @@\n-a\n+b\n@@ -40,2 +40,3 @@\n c\n+d\n e"))
        assert [(change.old, change.new) for change in found] == [
            (None, None),
            (1, None),
            (None, 1),
            (None, None),
            (40, 40),
            (None, 41),
            (41, 42),
        ]

    def test_the_file_headers_are_passed_over(self) -> None:
        assert all(change.text != "a.py" for change in changes_of(DIFF))

    def test_a_removed_line_that_reads_like_a_file_header_is_a_line(self) -> None:
        assert list(changes_of(diffed("q.sql", ["select 1;", "-- x", "select 2;"], ["select 1;", "select 2;"]))) == [
            Change("@", None, None, "@@ -1,3 +1,2 @@"),
            Change(" ", 1, 1, "select 1;"),
            Change("-", 2, None, "-- x"),
            Change(" ", 3, 2, "select 2;"),
        ]

    def test_an_added_line_that_reads_like_a_file_header_is_a_line(self) -> None:
        assert list(changes_of(diffed("q.hs", ["a", "b"], ["a", "++ y", "b"]))) == [
            Change("@", None, None, "@@ -1,2 +1,3 @@"),
            Change(" ", 1, 1, "a"),
            Change("+", None, 2, "++ y"),
            Change(" ", 2, 3, "b"),
        ]

    def test_a_line_saying_the_file_has_no_final_newline_is_not_a_line(self) -> None:
        found = list(changes_of("@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b"))
        assert [change.mark for change in found] == ["@", "-", "+"]

    def test_a_line_that_is_not_a_diffs_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a line of a unified diff"):
            list(changes_of("@@ -1 +1 @@\nnot marked"))

    def test_a_line_past_what_its_hunk_counts_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a line of a unified diff"):
            list(changes_of("@@ -1 +1 @@\n-a\n+b\n+c"))


class TestEveryCallStartsShut:
    """
    Whatever the tool and whether or not the call has come back, because the fold script takes every
    toggle as the reader's and a default that moved as a result landed would be recorded as a
    decision nobody made. What the calls that write did is the batch's diff below the panel.
    """

    @pytest.mark.parametrize("tool", ["create", "edit", "read", "list", "grep", "bash", "hand_off"])
    @pytest.mark.parametrize("returned", [None, Returned("success", "done")], ids=["out", "back"])
    def test_a_call_is_drawn_shut_and_says_so(self, tool: str, returned: Returned | None) -> None:
        drawn_shut = render(
            tool_block(ToolUse(tool=tool, arguments='{"path": "a.py"}', returned=returned), "panel-0-1", 0)
        )
        assert 'id="panel-0-1-tool-0" data-opens="shut"' in drawn_shut


class TestWhatAnOpenCallShows:
    """
    The body under a call's summary, drawn per tool where the console knows the tool and as
    argument rows and verbatim text where it does not.
    """

    def test_a_shell_command_stands_on_its_own_without_its_name(self) -> None:
        body = ran_a_pipe()
        assert '<div class="argument"><pre class="lines">' in body
        assert "argument__name" not in body

    def test_a_shell_command_is_coloured_as_one(self) -> None:
        assert '<span class="p">|</span>' in ran_a_pipe()

    def test_a_shell_command_is_not_wrapped_in_json(self) -> None:
        assert '{"command"' not in ran_a_pipe()

    def test_what_a_shell_command_gave_back_is_as_the_model_saw_it(self) -> None:
        assert "$ ls | wc -l" in ran_a_pipe()

    def test_every_other_argument_of_a_command_is_still_shown(self) -> None:
        body = drawn("bash", {"command": "sleep 200", "seconds": 300}, None)
        assert '<span class="argument__name">seconds</span> <code>300</code>' in body

    def test_an_edit_that_recorded_a_diff_is_drawn_as_that_and_nothing_else(self) -> None:
        body = drawn(
            "edit",
            {"path": "a.py", "operations": [{"op": "substitute", "at": "mkpv", "find": "1", "replace": "42"}]},
            Returned(outcome="success", content="edited a.py, now 3 lines\n\nlines 1-3:\n...", metadata={"diff": DIFF}),
        )
        assert "<dt>diff</dt>" in body
        assert 'data-gutter="2   " data-mark="-">-    return 1\n</span>' in body
        assert 'data-gutter="  2 " data-mark="+">+    return 42\n</span>' in body
        assert "called with" not in body
        assert "operations" not in body
        assert "returned" not in body

    def test_the_numbers_are_padded_to_the_widest_one(self) -> None:
        body = drawn(
            "edit", {"path": "a.py", "operations": []}, Returned("success", "", {"diff": "@@ -99 +99,2 @@\n a\n+b"})
        )
        assert 'data-gutter=" 99  99 "' in body
        assert 'data-gutter="    100 "' in body

    def test_an_edit_that_changed_nothing_says_so(self) -> None:
        body = drawn("edit", {"path": "a.py", "operations": []}, Returned("success", "edited a.py", {"diff": ""}))
        assert '<span class="tool__silent">no change</span>' in body

    def test_an_edit_that_was_refused_shows_what_it_asked_for_and_what_it_was_told(self) -> None:
        body = drawn(
            "edit",
            {"path": "a.py", "operations": [{"op": "substitute", "at": "zzzz", "find": "1", "replace": "42"}]},
            Returned(outcome="failed", content='{"error": "no line is anchored \'zzzz\'"}'),
        )
        assert "called with" in body
        assert '"at": "zzzz"' in body, "the operations, laid out"
        assert "returned" in body
        assert "no line is anchored" in body

    def test_a_diff_recorded_by_anything_but_an_edit_is_not_read_as_one(self) -> None:
        body = drawn("other", {"x": 1}, Returned("success", "done", {"diff": DIFF}))
        assert "called with" in body
        assert "<dt>diff</dt>" not in body

    def test_a_read_is_coloured_by_the_grammar_its_path_names(self) -> None:
        assert '<span class="line"><span class="k">def</span>' in read_a_python_file()

    def test_a_blank_line_a_read_returned_is_a_blank_line(self) -> None:
        assert '<span class="line">\n</span>' in read_a_python_file()

    def test_the_line_a_read_wrote_itself_is_marked_as_the_tools(self) -> None:
        assert '<span class="line" data-said>a.py, 3 lines\n</span>' in read_a_python_file()

    @pytest.mark.parametrize("drawn_in_front", ["qwrt", "│"], ids=["anchor", "bar"])
    def test_a_read_is_drawn_without_its_anchors(self, drawn_in_front: str) -> None:
        assert drawn_in_front not in read_a_python_file()

    def test_a_read_of_a_file_no_grammar_is_known_for_is_left_uncoloured(self) -> None:
        body = drawn("read", {"path": "notes"}, Returned("success", PYTHON_READ))
        assert '<span class="line">def f():\n</span>' in body

    def test_a_search_loses_its_anchors_and_takes_no_grammar(self) -> None:
        body = drawn("grep", {"pattern": "def"}, Returned("success", "a.py, lines 1-1 of 3:\nqwrt│def f():"))
        assert '<span class="line" data-said>a.py, lines 1-1 of 3:\n</span><span class="line">def f():\n</span>' in body

    def test_what_a_create_still_out_was_handed_is_coloured_by_the_grammar_its_path_names(self) -> None:
        body = drawn("create", {"path": "b.py", "content": "import os\n"}, None)
        assert '<span class="argument__name">content</span>' in body
        assert '<span class="kn">import</span>' in body

    def test_a_create_that_wrote_its_file_shows_the_reply_alone(self) -> None:
        """The reply is the new file under a line saying so, which is the content with a confirmation on top."""
        body = drawn(
            "create",
            {"path": "b.py", "content": "import os\n"},
            Returned("success", "created b.py, 1 line\n\nqwrt│import os"),
        )
        assert "called with" not in body
        assert '<span class="line" data-said>created b.py, 1 line\n</span>' in body
        assert '<span class="line"><span class="kn">import</span>' in body

    def test_a_create_that_was_refused_still_shows_what_it_was_handed(self) -> None:
        body = drawn(
            "create",
            {"path": "b.py", "content": "import os\n"},
            Returned("failed", '{"error": "\'b.py\' already exists; `create` never overwrites"}'),
        )
        assert '<span class="argument__name">content</span>' in body
        assert "already exists" in body

    def test_a_string_argument_that_runs_to_lines_is_a_block_and_one_that_does_not_is_inline(self) -> None:
        body = drawn("other", {"one": "a", "many": "a\nb"}, None)
        assert '<span class="argument__name">one</span> <code>a</code>' in body
        assert '<span class="argument__name">many</span> <pre class="lines">' in body

    def test_an_argument_with_a_shape_is_json_laid_out(self) -> None:
        body = drawn("other", {"deep": {"a": [1, 2]}}, None)
        assert (
            '<span class="argument__name">deep</span> <pre><code>{\n  "a": [\n    1,\n    2\n  ]\n}</code></pre>'
            in body
        )

    def test_a_call_that_is_not_an_object_is_shown_as_it_arrived(self) -> None:
        body = render(call_body(ToolUse(tool="bash", arguments='{"command": "ls"', returned=None)))
        assert '<pre><code>{"command": "ls"</code></pre>' in body

    def test_a_call_still_out_shows_what_it_was_handed_and_nothing_else(self) -> None:
        body = drawn("read", {"path": "a.py"}, None)
        assert "called with" in body
        assert "returned" not in body

    def test_what_any_other_tool_returned_is_verbatim_with_its_urls_linked(self) -> None:
        body = drawn("hand_off", {}, Returned("success", "see https://example.com/x"))
        assert '<a href="https://example.com/x" referrerpolicy="no-referrer">https://example.com/x</a>' in body


GIT_DIFF = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 def f():
-    return 1
+    return 42
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,1 @@
+hello
"""


class TestABlocksBatch:
    """The net change a whole batch made, read out of the git diff the snapshot recorded."""

    def test_each_file_is_named_with_its_hunks(self) -> None:
        assert [(path, len(changes)) for path, changes in changes_by_file(GIT_DIFF)] == [
            ("a.py", 4),
            ("new.txt", 2),
        ]

    @pytest.mark.parametrize("path", ["a.py", "new.txt"])
    def test_each_file_is_headed_by_its_path(self, path: str) -> None:
        body = render(block_diff_element(changes_by_file(GIT_DIFF)))
        assert f'<span class="line" data-said>{path}\n</span>' in body

    @pytest.mark.parametrize("line", ['data-mark="-">-    return 1\n</span>', 'data-mark="+">+hello\n</span>'])
    def test_each_line_keeps_its_mark(self, line: str) -> None:
        assert line in render(block_diff_element(changes_by_file(GIT_DIFF)))

    @pytest.mark.parametrize(
        ("header", "path"),
        [
            ("--- a/my b/file.txt\t\n+++ b/my b/file.txt\t", "my b/file.txt"),
            ('--- "a/caf\\303\\251.txt"\n+++ "b/caf\\303\\251.txt"', "café.txt"),
            ('--- "a/q\\"t\\\\x.txt"\n+++ "b/q\\"t\\\\x.txt"', 'q"t\\x.txt'),
            ("--- a/gone.txt\n+++ /dev/null", "gone.txt"),
            ("--- /dev/null\n+++ b/new.txt", "new.txt"),
        ],
        ids=["spaced", "non-ascii", "quote-and-backslash", "removed", "added"],
    )
    def test_the_path_is_the_one_git_named_in_the_files_header(self, header: str, path: str) -> None:
        """Each header is the one git prints for that case, down to the tab after a name with a space."""
        [(found, _)] = changes_by_file(f"diff --git a/x b/x\nindex 111..222 100644\n{header}\n@@ -1 +1 @@\n-a\n+b\n")
        assert found == path

    @pytest.mark.parametrize("line", ["-- x", "++ y"])
    def test_a_line_that_reads_like_a_file_header_is_kept(self, line: str) -> None:
        diff = f"diff --git a/q.sql b/q.sql\n--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,2 @@\n-{line}\n+{line}\n kept\n"
        [(_, changes)] = changes_by_file(diff)
        assert [(change.mark, change.text) for change in changes[1:]] == [("-", line), ("+", line), (" ", "kept")]

    def test_a_binary_change_has_no_lines_and_is_left_out(self) -> None:
        binary = "diff --git a/img.png b/img.png\nindex 111..222 100644\nBinary files a/img.png and b/img.png differ\n"
        assert changes_by_file(binary) == ()

    def test_a_batch_that_changed_only_binary_files_draws_no_fold(self) -> None:
        binary = "diff --git a/img.png b/img.png\nindex 111..222 100644\nBinary files a/img.png and b/img.png differ\n"
        assert batch_element("panel-1-4", binary) is None

    def test_the_summary_says_how_many_files_and_lines_went_in_and_out(self) -> None:
        body = render(batch_element("panel-1-4", GIT_DIFF))
        assert '<span class="batch__files">2 files</span>' in body
        assert '<span class="batch__added">+2</span> <span class="batch__removed">\N{MINUS SIGN}1</span>' in body

    def test_a_short_diff_is_drawn_open(self) -> None:
        body = render(batch_element("panel-1-4", GIT_DIFF))
        assert 'id="panel-1-4-diff" open data-opens="open"' in body

    @pytest.mark.parametrize(("added", "starts"), [(LONGEST_OPEN_DIFF - 2, "open"), (LONGEST_OPEN_DIFF - 1, "shut")])
    def test_a_diff_longer_than_the_knob_is_drawn_shut(self, added: int, starts: str) -> None:
        """One file's header, its hunk header, and its lines: exactly the knob is still open."""
        long = f"diff --git a/big b/big\n--- /dev/null\n+++ b/big\n@@ -0,0 +1,{added} @@\n" + "+x\n" * added
        body = render(batch_element("panel-1-4", long))
        assert f'id="panel-1-4-diff"{" open" if starts == "open" else ""} data-opens="{starts}"' in body
