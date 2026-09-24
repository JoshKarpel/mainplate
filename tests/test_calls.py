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
from mainplate.calls import starts_open
from mainplate.conversation import Returned
from mainplate.conversation import ToolUse
from mainplate.pages import tool_block

PYTHON_READ = "a.py, 3 lines\n\nqwrt│def f():\n----│\nmkpv│    return 1"

# One literal, because a context line that is blank in the file is a single space in the diff and
# a trailing space is what every editor strips off the end of a line.
DIFF = "--- a.py\n+++ a.py\n@@ -1,3 +1,3 @@\n def f():\n-    return 1\n+    return 42\n "


def drawn(tool: str, arguments: dict[str, object], returned: Returned | None) -> str:
    return render(call_body(ToolUse(tool=tool, arguments=json.dumps(arguments), returned=returned)))


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

    def test_a_line_that_is_not_a_diffs_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a line of a unified diff"):
            list(changes_of("@@ -1 +1 @@\nnot marked"))


class TestWhichCallsStartOpen:
    """
    Decided by the tool alone, never by whether the call has come back, because the fold script
    takes every toggle as the reader's and a default that moved as a result landed would be recorded
    as a decision nobody made.
    """

    @pytest.mark.parametrize("tool", ["create"])
    def test_a_call_that_writes_something_is_drawn_open(self, tool: str) -> None:
        assert starts_open(tool)

    @pytest.mark.parametrize("tool", ["read", "list", "grep", "bash", "hand_off", "edit"])
    def test_every_other_call_is_drawn_shut(self, tool: str) -> None:
        assert not starts_open(tool)

    def test_the_fold_says_so_in_both_attributes(self) -> None:
        opened = render(tool_block(ToolUse(tool="create", arguments='{"path": "a.py"}', returned=None), "panel-0-1", 0))
        shut = render(tool_block(ToolUse(tool="read", arguments='{"path": "a.py"}', returned=None), "panel-0-1", 0))
        assert 'id="panel-0-1-tool-0" open data-opens="open"' in opened
        assert 'id="panel-0-1-tool-0" data-opens="shut"' in shut


class TestWhatAnOpenCallShows:
    """
    The body under a call's summary, drawn per tool where the console knows the tool and as
    argument rows and verbatim text where it does not.
    """

    def test_a_shell_command_is_coloured_as_one_and_not_wrapped_in_json(self) -> None:
        body = drawn(
            "bash", {"command": "ls | wc -l"}, Returned(outcome="success", content="$ ls | wc -l\n\n3\n\nexit 0")
        )
        assert '<div class="argument"><pre class="lines">' in body, "the command stands on its own, unlabelled"
        assert "argument__name" not in body
        assert '<span class="p">|</span>' in body, "the pipe is a token"
        assert '{"command"' not in body
        assert "$ ls | wc -l" in body, "and what came back is as the model saw it"

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

    def test_a_read_is_coloured_by_the_grammar_its_path_names_with_its_anchors_left_out(self) -> None:
        body = drawn("read", {"path": "a.py"}, Returned("success", PYTHON_READ))
        assert '<span class="line"><span class="k">def</span>' in body
        assert '<span class="line">\n</span>' in body, "a blank line of the file is a blank line"
        assert '<span class="line" data-said>a.py, 3 lines\n</span>' in body, "the tool's own line is marked"
        assert "qwrt" not in body, "the anchor is nowhere on the page"
        assert "│" not in body

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

    def test_a_header_is_drawn_per_file_and_the_marks_are_kept(self) -> None:
        body = render(block_diff_element(GIT_DIFF))

        assert '<span class="line" data-said>a.py\n</span>' in body
        assert '<span class="line" data-said>new.txt\n</span>' in body
        assert 'data-mark="-">-    return 1\n</span>' in body
        assert 'data-mark="+">+hello\n</span>' in body

    def test_a_binary_change_has_no_lines_and_is_left_out(self) -> None:
        binary = "diff --git a/img.png b/img.png\nindex 111..222 100644\nBinary files a/img.png and b/img.png differ\n"
        assert changes_by_file(binary) == ()
