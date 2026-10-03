from __future__ import annotations

import pytest
from without_asgi import RawHeaders

from mainplate.origins import is_crossing

# The console as a browser reached it, and a dev server on another port of the same host.
CONSOLE = b"vm.example:8100"
ELSEWHERE = b"http://vm.example:5173"


class TestWhichWritesCross:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_a_read_is_answered_from_wherever_it_came(self, method: str) -> None:
        assert not is_crossing(method, ((b"sec-fetch-site", b"cross-site"),))

    @pytest.mark.parametrize("site", [b"same-origin", b"none"])
    def test_a_write_from_the_console_s_own_page_or_the_address_bar_is_answered(self, site: bytes) -> None:
        assert not is_crossing("POST", ((b"sec-fetch-site", site),))

    @pytest.mark.parametrize("site", [b"same-site", b"cross-site"])
    def test_a_write_from_any_other_page_is_refused(self, site: bytes) -> None:
        """`same-site` is the case this exists for: a page on another port of the console's host."""
        assert is_crossing("POST", ((b"sec-fetch-site", site),))

    def test_the_browser_s_own_account_decides_over_origin(self) -> None:
        """
        A proxy may rewrite `Host`, and over HTTPS that must not refuse the console's own writes,
        so where `Sec-Fetch-Site` is sent `Origin` is never compared.
        """
        sent: RawHeaders = ((b"sec-fetch-site", b"same-origin"), (b"origin", ELSEWHERE), (b"host", CONSOLE))
        assert not is_crossing("POST", sent)

    def test_without_fetch_metadata_an_origin_matching_the_host_is_answered(self) -> None:
        assert not is_crossing("POST", ((b"origin", b"http://" + CONSOLE), (b"host", CONSOLE)))

    @pytest.mark.parametrize(
        ("origin", "why"),
        [
            (ELSEWHERE, "another port"),
            (b"http://elsewhere.example:8100", "another host"),
            (b"null", "an opaque origin, such as an artifact's frame"),
        ],
    )
    def test_without_fetch_metadata_any_other_origin_is_refused(self, origin: bytes, why: str) -> None:
        assert is_crossing("POST", ((b"origin", origin), (b"host", CONSOLE)))

    def test_a_write_saying_nothing_about_where_it_came_from_is_not_a_browser_s_and_is_answered(self) -> None:
        assert not is_crossing("POST", ((b"host", CONSOLE),))
