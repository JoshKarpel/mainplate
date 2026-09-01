// The console's own behaviour: the theme, and everything in the rail.
//
// It exists because of one property of this page. The transcript is rendered whole by the server
// and swapped in again whenever an answer arrives, so nothing a reader does *to* the conversation
// can be kept in the conversation's own markup: a mark, an unfolded tool call, the panel they
// landed on, would each be merged away by the next poll a second later.
//
// So this holds all of it as values, off to one side, and projects them back onto whatever markup
// is currently on screen. `repaint()` is that projection and it is idempotent, which is what lets
// the same function serve the first render, every swap after it, and every press of a control.
// Nothing here reads state back out of the DOM in order to decide anything.
//
// Everything it drives is an enhancement. With this file absent the page still renders, still
// posts messages, still polls for answers, and every tool call is still a `<details>` a reader can
// open; what they lose is the search, the dock, the key, and the theme toggle.
(() => {
  "use strict";

  // --- Values ------------------------------------------------------------
  //
  // No document, no storage, no clock: everything below can be reasoned about on its own.

  const THEMES = ["system", "light", "dark"];

  // Storage is arbitrary text, and a value written by an older build or by a hand in the console
  // must not leave the page in a scheme it has no rules for.
  const asTheme = (held) => (THEMES.includes(held) ? held : "system");

  // Where a needle falls in a haystack, case-insensitively. Finding the spans is the whole of the
  // search that does not need a document; cutting the text node at them is the part that does.
  const spans = (text, needle) => {
    const found = [];
    if (!needle) return found;
    const hay = text.toLowerCase();
    const query = needle.toLowerCase();
    let from = hay.indexOf(query);
    while (from !== -1) {
      found.push({ from, to: from + needle.length });
      from = hay.indexOf(query, from + needle.length);
    }
    return found;
  };

  // Which panel the reader is on: the last one whose top has scrolled to or above the threshold.
  // The threshold clears a panel's own scroll-margin, so the panel just stepped to counts as the
  // current one rather than as the one before it.
  const currentIndex = (tops, threshold) => {
    let current = -1;
    tops.forEach((top, index) => {
      if (top <= threshold) current = index;
    });
    return current;
  };

  // Clamped rather than wrapped: an arrow at the end of a conversation is spent, and wrapping
  // would take a reader who pressed once too often back to the opposite end of what they just read.
  const stepIndex = (tops, direction, threshold) => {
    const current = currentIndex(tops, threshold);
    return Math.min(Math.max(current + direction, 0), tops.length - 1);
  };

  // --- Storage -----------------------------------------------------------
  //
  // A privilege the page can be opened without, so every read answers with nothing rather than
  // throwing and every write is allowed to do nothing at all.

  const held = (key) => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  };

  const hold = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch {}
  };

  // --- Theme -------------------------------------------------------------
  //
  // The one thing that runs before the document exists. This script is a blocking tag in the head
  // precisely so the reader's choice is pinned on <html> before the first paint: applied any later
  // and a page opened dark flashes light on the way there.

  const THEME_KEY = "mainplate:theme";

  const applyTheme = (theme) => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  };

  applyTheme(asTheme(held(THEME_KEY)));

  const start = () => {
    // The theme is the reader's and holds across every session; everything else below is a fact
    // about one conversation, so it is stored under that conversation's own id.
    const session = document.body.dataset.session || "?";
    const scoped = (name) => `mainplate:${name}:${session}`;

    const transcript = () => document.getElementById("transcript");

    // --- What the reader has decided ------------------------------------
    //
    // The whole of the state this file keeps. Everything else is a function of it.

    let aside = new Set(); // kinds the key has switched off
    let landed = null; // the panel the console last put the reader on
    let opened = new Set(); // tool calls the reader has unfolded
    let following = held(scoped("follow")) !== "no"; // pin the end as answers arrive
    let query = "";
    let hits = [];
    let at = -1;

    try {
      const stored = JSON.parse(held(scoped("aside")) || "[]");
      // What is stored is the kinds set *aside*, not the ones in play, so a kind added by a later
      // build arrives in play rather than silently missing from a reader's stored list.
      if (Array.isArray(stored)) aside = new Set(stored);
    } catch {}

    // --- Reading the conversation ---------------------------------------

    const panelsIn = (side) => {
      const box = transcript();
      if (!box) return [];
      return Array.from(box.querySelectorAll(".panel")).filter(
        (panel) =>
          !aside.has(panel.dataset.kind) &&
          (!side || panel.dataset.side === side) &&
          panel.getClientRects().length > 0,
      );
    };

    const atEnd = (box) => box.scrollHeight - box.scrollTop - box.clientHeight < 8;

    // Ours, so the listener that releases following can tell a scroll the reader asked for from
    // one this file performed. Without it, following would switch itself off the instant it worked.
    let ours = false;

    const toEnd = () => {
      const box = transcript();
      if (!box) return;
      ours = true;
      box.scrollTop = box.scrollHeight;
      requestAnimationFrame(() => {
        ours = false;
      });
    };

    // --- Projection ------------------------------------------------------
    //
    // Everything the reader has decided, put back onto the markup currently on screen. Called once
    // at the start and again after every swap, and safe to call at any other time: it states the
    // whole of what the transcript should look like rather than the difference from anything.

    const paintAside = () => {
      const box = transcript();
      if (!box) return;
      box.querySelectorAll(".panel").forEach((panel) => {
        if (aside.has(panel.dataset.kind)) panel.dataset.aside = "";
        else delete panel.dataset.aside;
      });
      document.querySelectorAll(".key__chip").forEach((chip) => {
        chip.setAttribute("aria-pressed", String(!aside.has(chip.dataset.kind)));
      });
    };

    const paintLanded = () => {
      const box = transcript();
      if (!box) return;
      box.querySelectorAll("[data-landed]").forEach((panel) => delete panel.dataset.landed);
      if (!landed) return;
      const panel = box.querySelector(`#${CSS.escape(landed)}`);
      if (panel) panel.dataset.landed = "";
    };

    const paintFolds = () => {
      const box = transcript();
      if (!box) return;
      // Only what the reader opened is forced. A call still waiting on its result is rendered open
      // by the server, and leaving that alone is what lets the server say so.
      box.querySelectorAll("details.tool").forEach((fold) => {
        if (opened.has(fold.id)) fold.open = true;
      });
    };

    const paintFollow = () => {
      const toggle = document.querySelector('[data-follow="toggle"]');
      if (toggle) toggle.setAttribute("aria-pressed", String(following));
    };

    // --- Search ----------------------------------------------------------

    const clearHits = () => {
      const box = transcript();
      // Nothing marked and nothing asked for is the usual case while a turn is being answered, and
      // this runs once a second then: `normalize()` walks the whole conversation, so it is worth
      // not doing when there is provably nothing to undo.
      if (!box || (!hits.length && !query)) return;
      box.querySelectorAll("mark.hit").forEach((mark) => {
        mark.replaceWith(document.createTextNode(mark.textContent));
      });
      box.normalize();
      hits = [];
    };

    const markHits = () => {
      const box = transcript();
      if (!box || !query) return;
      const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!node.nodeValue.trim()) continue;
        const parent = node.parentElement;
        if (!parent) continue;
        // A panel's own label and permalink are chrome, not conversation.
        if (parent.closest(".panel__meta")) continue;
        // Only the kinds the reader left in play, so the count is of what they are looking at.
        const panel = parent.closest(".panel");
        if (panel && aside.has(panel.dataset.kind)) continue;
        nodes.push(node);
      }
      for (const node of nodes) {
        const text = node.nodeValue;
        const found = spans(text, query);
        if (!found.length) continue;
        const pieces = document.createDocumentFragment();
        let pos = 0;
        for (const { from, to } of found) {
          if (from > pos) pieces.appendChild(document.createTextNode(text.slice(pos, from)));
          const mark = document.createElement("mark");
          mark.className = "hit";
          mark.textContent = text.slice(from, to);
          pieces.appendChild(mark);
          hits.push(mark);
          pos = to;
        }
        if (pos < text.length) pieces.appendChild(document.createTextNode(text.slice(pos)));
        node.parentNode.replaceChild(pieces, node);
      }
    };

    const field = document.querySelector(".search__input");
    const count = document.querySelector(".search__count");
    const steppers = document.querySelectorAll(".search__nav");

    // `go` is what separates a reader asking for a hit from the page noticing one. Going to a hit
    // opens the folds around it and scrolls to it; merely re-counting after a swap must do
    // neither, or an answer arriving would drag a reader halfway down the hit list back to the top.
    const paintSearch = (go) => {
      hits.forEach((hit) => hit.classList.remove("current"));
      steppers.forEach((button) => {
        button.disabled = hits.length === 0;
      });
      if (!count) return;
      if (!query) {
        count.textContent = "";
        return;
      }
      if (!hits.length) {
        count.textContent = "no matches";
        return;
      }
      const hit = hits[at];
      hit.classList.add("current");
      if (go) {
        for (let node = hit.parentElement; node; node = node.parentElement) {
          if (node.tagName === "DETAILS") node.open = true;
        }
        hit.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      count.textContent = `${at + 1}/${hits.length}`;
    };

    const research = (go) => {
      const wanted = at;
      clearHits();
      markHits();
      // The reader's place in the count is theirs, so it is kept rather than reset: a conversation
      // grows at its end, so the hit they were on is the hit at the same ordinal.
      at = hits.length ? Math.min(Math.max(go ? 0 : wanted, 0), hits.length - 1) : -1;
      paintSearch(go);
    };

    const repaint = () => {
      paintAside();
      paintLanded();
      paintFolds();
      paintFollow();
      research(false);
      if (following) toEnd();
    };

    // --- Landing ---------------------------------------------------------

    // Every way of arriving at a panel goes through here, so the URL always names where the reader
    // is. `replaceState` rather than assigning `location.hash`, so twenty steps do not cost twenty
    // presses of Back; it performs no scroll of its own, hence the explicit one, which honours the
    // panel's own `scroll-margin-top`.
    //
    // The panel is marked as well as named, because `:target` answers to navigation and
    // `replaceState` is not navigation. The stylesheet draws the mark and `:target` alike, so a
    // landing and an arrival by link read the same.
    const land = (panel) => {
      if (!panel) return;
      following = false;
      hold(scoped("follow"), "no");
      paintFollow();
      landed = panel.id;
      paintLanded();
      try {
        history.replaceState(null, "", `#${panel.id}`);
      } catch {
        // A page served from somewhere `replaceState` refuses is still perfectly navigable.
      }
      panel.scrollIntoView({ block: "start", behavior: "auto" });
    };

    // Following a panel's own permalink is the one way of arriving that does *not* go through
    // `land`: the browser moves the hash itself, so `:target` follows along and `landed` does not.
    // Left unwired the two disagree the moment a second link is clicked, and because the
    // stylesheet draws them alike, the page shows two panels highlighted with no way to tell which
    // one the reader is actually on. Syncing here rather than dropping `data-landed` altogether,
    // because `replaceState` is still not navigation and `:target` still cannot see it.
    const wireHash = () => {
      window.addEventListener("hashchange", () => {
        const named = decodeURIComponent((location.hash || "").replace(/^#/, ""));
        landed = named || null;
        if (named) {
          following = false;
          hold(scoped("follow"), "no");
          paintFollow();
        }
        paintLanded();
      });
    };

    // --- The controls ----------------------------------------------------

    const wireKey = () => {
      document.querySelectorAll(".key__chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const kind = chip.dataset.kind;
          if (aside.has(kind)) aside.delete(kind);
          else aside.add(kind);
          hold(scoped("aside"), JSON.stringify([...aside]));
          paintAside();
          research(false);
        });
      });
    };

    const wireSearch = () => {
      if (!field) return;
      field.addEventListener("input", () => {
        query = field.value;
        research(true);
      });
      field.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        step(event.shiftKey ? -1 : 1);
      });
      const step = (delta) => {
        if (!hits.length) return;
        at = (at + delta + hits.length) % hits.length;
        paintSearch(true);
      };
      steppers.forEach((button) => {
        button.addEventListener("click", () => step(button.dataset.search === "prev" ? -1 : 1));
      });
    };

    const wireDock = () => {
      document.querySelectorAll("[data-step]").forEach((button) => {
        button.addEventListener("click", () => {
          const box = transcript();
          if (!box) return;
          const panels = panelsIn(button.dataset.side);
          const top = box.getBoundingClientRect().top;
          const tops = panels.map((panel) => panel.getBoundingClientRect().top - top);
          land(panels[stepIndex(tops, Number(button.dataset.step), 40)]);
        });
      });

      document.querySelectorAll("[data-leap]").forEach((button) => {
        button.addEventListener("click", () => {
          const panels = panelsIn(null);
          land(button.dataset.leap === "start" ? panels[0] : panels[panels.length - 1]);
        });
      });

      document.querySelectorAll("[data-fold]").forEach((button) => {
        button.addEventListener("click", () => {
          const box = transcript();
          if (!box) return;
          const open = button.dataset.fold === "open";
          box.querySelectorAll("details.tool").forEach((fold) => {
            fold.open = open;
            if (open) opened.add(fold.id);
            else opened.delete(fold.id);
          });
        });
      });

      const toggle = document.querySelector('[data-follow="toggle"]');
      if (toggle) {
        toggle.addEventListener("click", () => {
          following = !following;
          hold(scoped("follow"), following ? "yes" : "no");
          paintFollow();
          if (following) toEnd();
        });
      }
    };

    // A reader who scrolls away from the end has stopped following, whether they used the wheel,
    // the keyboard, or the scrollbar. Watching the scroll itself covers all three, which three
    // separate input listeners would not; `ours` is what keeps this file's own scrolls out of it.
    const wireScroll = () => {
      document.addEventListener(
        "scroll",
        (event) => {
          const box = transcript();
          if (!box || event.target !== box || ours || !following) return;
          if (atEnd(box)) return;
          following = false;
          hold(scoped("follow"), "no");
          paintFollow();
        },
        true,
      );
    };

    // Which tool calls the reader has open, kept as they press rather than read back later: a
    // `<details>` closed by the next swap would otherwise look to this file like one they shut.
    const wireFolds = () => {
      document.addEventListener("toggle", (event) => {
        const fold = event.target;
        if (!(fold instanceof HTMLDetailsElement) || !fold.classList.contains("tool")) return;
        if (fold.open) opened.add(fold.id);
        else opened.delete(fold.id);
      }, true);
    };

    const wireTheme = () => {
      const buttons = document.querySelectorAll("[data-theme-choice]");
      const paint = (theme) => {
        buttons.forEach((button) => {
          button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme));
        });
      };
      paint(asTheme(held(THEME_KEY)));
      buttons.forEach((button) => {
        button.addEventListener("click", () => {
          const theme = button.dataset.themeChoice;
          applyTheme(theme);
          hold(THEME_KEY, theme);
          paint(theme);
        });
      });
    };

    // Where the rail has room to stand beside the conversation there is nothing to unclasp, and
    // the stylesheet does not draw this at all. Where it has not, the rail would lie over the very
    // text it exists to navigate, so it is held shut and this is the press that lets it out.
    // Nothing here measures the window, so the two cannot disagree about where the rail fits.
    const wireClasp = () => {
      const rail = document.querySelector(".rail");
      const clasp = rail && rail.querySelector(".rail__clasp");
      if (!rail || !clasp) return;
      const open = (wanted) => {
        if (wanted) rail.dataset.open = "";
        else delete rail.dataset.open;
        clasp.setAttribute("aria-expanded", String(wanted));
      };
      clasp.addEventListener("click", () => open(clasp.getAttribute("aria-expanded") !== "true"));
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") open(false);
      });
    };

    // Shift-Enter sends, and plain Enter still breaks the line. That way round because a message
    // here is prose that often wants a second paragraph and a fenced block, and a box where the
    // obvious key sends is a box you cannot write one in without learning a second key first.
    //
    // `requestSubmit` rather than `submit`, and the difference is the whole of why this works on
    // both pages: `submit()` posts without dispatching a `submit` event, so htmx would never see
    // the send on a session page and the browser would navigate away from a conversation. It also
    // runs the form's own validation, so an empty box refuses here exactly as it refuses the
    // button, rather than posting a message nobody typed.
    //
    // Delegated, because the composer is rebuilt whenever a page is: this is one listener for every
    // box on every page rather than one wired per form at load.
    const wireSend = () => {
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" || !event.shiftKey) return;
        const box = event.target;
        if (!(box instanceof HTMLTextAreaElement) || box.name !== "prompt" || !box.form) return;
        event.preventDefault();
        box.form.requestSubmit();
      });
    };

    // --- Swaps -------------------------------------------------------------
    //
    // The marks come off before the swap and go back on after it. Taking them off first is not
    // tidiness: morphing merges the incoming markup into the DOM already on screen, and elements
    // this file put there are not in that markup, so leaving them would make the merge reconcile
    // nodes the server has never heard of.
    const wireSwaps = () => {
      document.addEventListener("htmx:before:swap", (event) => {
        if (event.target === transcript()) clearHits();
      });
      document.addEventListener("htmx:after:swap", () => repaint());
    };

    wireKey();
    wireSearch();
    wireDock();
    wireScroll();
    wireFolds();
    wireTheme();
    wireClasp();
    wireSend();
    wireSwaps();
    wireHash();

    // A panel named in the URL is where the reader asked to be, and outranks following the end.
    const named = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    if (named) {
      following = false;
      landed = named;
    }
    repaint();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
