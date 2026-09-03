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
// posts messages, and every tool call is still a `<details>` a reader can open; what they lose is
// the search, the dock, the key, the theme toggle, the copy buttons on a panel and on the code in
// it, and the live connection that would have brought an answer without a reload.
(() => {
  "use strict";

  // --- Values ------------------------------------------------------------
  //
  // No document, no storage, no clock: everything below can be reasoned about on its own.

  const THEMES = ["system", "light", "dark"];

  // Everything in the transcript that folds: a tool call, and a command the person ran. Named once
  // because two places act on the set - putting a reader's folds back after a swap, and the dock's
  // fold-everything buttons - and a kind added to one and not the other is a fold that reopens
  // itself on the next render.
  const FOLDS = "details.tool, details.ran";

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

    let muted = new Set(); // kinds the key has switched off
    let landed = null; // the panel the console last put the reader on
    let opened = new Set(); // tool calls the reader has unfolded
    let query = "";
    let hits = [];
    let at = -1;

    // Pinned to the end as answers arrive, which is where a page starts and where scrolling back to
    // the bottom returns it. Not stored, and that is the difference from everything else here: the
    // rest of this state is a decision the reader made *about a conversation* and should find again
    // tomorrow, where this one is a mode you fall out of by scrolling up and back into by scrolling
    // down. Kept across a reload it would be a page that opens somewhere the reader has to notice
    // and undo, rather than at the end of what was said.
    let following = true;
    let signatures = new Map(); // what each panel said, so a change can be told from a repaint
    let sentFrom = null; // the box a message has just left, so the cursor can be put back in it
    let copied = null; // the panel whose copy button is saying so
    let saying = null; // and the timer that stops it saying it

    let shelf = []; // text kept and not sent, as {name, text}

    // Whether the box is a command box. Not stored, and that is the same line `following` is on:
    // this is a mode within a visit rather than a decision about a conversation, so carrying it
    // across a reload would be a page that opens as something the reader has to notice and undo.
    let commanding = false;

    try {
      const stored = JSON.parse(held(scoped("muted")) || "[]");
      // What is stored is the kinds *muted*, not the ones in play, so a kind added by a later build
      // arrives in play rather than silently missing from a reader's stored list.
      if (Array.isArray(stored)) muted = new Set(stored);
    } catch {}

    // --- The shelf --------------------------------------------------------
    //
    // Unsent text, kept for this conversation. It is the reader's own and the server is never told
    // any of it, which is what lets it live here at all: a page with this store wiped renders exactly
    // what one without it does, because none of this is a word of the conversation until it is sent.

    const SHELF = scoped("shelf");

    const slots = (raw) => {
      try {
        const stored = JSON.parse(raw || "[]");
        if (!Array.isArray(stored)) return [];
        return stored.filter((slot) => slot && typeof slot.text === "string");
      } catch {
        return [];
      }
    };

    // A branch inherits what its parent kept, which is the copy `Service.fork` cannot make:
    // the server has never seen a draft, so it says which conversation this one came from and the
    // page holding both does the rest.
    //
    // Only where this session has *no* shelf of its own yet, which is a different thing from an empty
    // one: a reader who cleared theirs has a stored `[]` and must not have the parent's handed back
    // on the next load. Copied rather than read through, so the two go their own ways exactly as the
    // turns they were forked beside do.
    const inherited = () => {
      const parent = document.body.dataset.forkedFrom;
      if (!parent) return [];
      const taken = slots(held(`mainplate:shelf:${parent}`));
      if (taken.length) hold(SHELF, JSON.stringify(taken));
      return taken;
    };

    shelf = held(SHELF) === null ? inherited() : slots(held(SHELF));

    const keepShelf = () => hold(SHELF, JSON.stringify(shelf));

    // What a slot is called: its first line, the way a session is named after its first message. Cut
    // short because this is a label in a seventeen-rem column and the whole text is one click away.
    const labelled = (text) => {
      const first = text.split("\n").find((line) => line.trim()) || "";
      const cut = first.trim();
      return cut.length > 42 ? `${cut.slice(0, 41)}…` : cut;
    };

    const composerBox = () => document.querySelector('.composer textarea[name="prompt"]');

    // --- Reading the conversation ---------------------------------------

    const panelsIn = (side) => {
      const box = transcript();
      if (!box) return [];
      return Array.from(box.querySelectorAll(".panel")).filter(
        (panel) =>
          !muted.has(panel.dataset.kind) &&
          (!side || panel.dataset.side === side) &&
          panel.getClientRects().length > 0,
      );
    };

    // What one arrow steps over, which the button says rather than this inferring. Panels are
    // narrowed by the key and by side; the rules that open each turn are not, because a turn is not
    // one of the kinds the key switches off - it is the thing those kinds are inside of.
    //
    // `.rule--turn` and not every rule: a rule now stands at every model request, so a turn with
    // four round trips in it would otherwise give the turn arrows four stops and stop meaning turns.
    const stopsFor = (button) => {
      const box = transcript();
      if (!box) return [];
      if (button.dataset.stop !== "turn") return panelsIn(button.dataset.side);
      return Array.from(box.querySelectorAll(".rule--turn")).filter((rule) => rule.getClientRects().length > 0);
    };

    const atEnd = (box) => box.scrollHeight - box.scrollTop - box.clientHeight < 8;

    // Ours, so the listener that follows the reader's position can tell a scroll they asked for
    // from one this file performed. It has to cover both directions, because that listener now
    // decides following in both: without it, following would switch itself off the instant it
    // worked, *and* a landing on the last panel would switch it back on the instant it was
    // deliberately switched off.
    let ours = false;

    const scrolling = (move) => {
      ours = true;
      move();
      requestAnimationFrame(() => {
        ours = false;
      });
    };

    const toEnd = () => {
      const box = transcript();
      if (!box) return;
      scrolling(() => {
        box.scrollTop = box.scrollHeight;
      });
    };

    // The session being read, brought into the list of them. On a narrow window that list is a
    // strip scrolling sideways, so the conversation on screen can be off one end of it; on a wide
    // one it is a column long enough to put the current session below the fold. One call answers
    // both, and nothing here knows which shape it is looking at: `nearest` scrolls the list on
    // whichever axis it actually scrolls on, and does nothing when the session is already showing.
    //
    // Not through `scrolling`, unlike every other scroll this file performs: the listener that
    // guard exists for watches the transcript, and this moves a different box entirely.
    const toCurrentSession = () => {
      const current = document.querySelector(".sessions .session.current");
      if (current) current.scrollIntoView({ block: "nearest", inline: "center" });
    };

    // --- Projection ------------------------------------------------------
    //
    // Everything the reader has decided, put back onto the markup currently on screen. Called once
    // at the start and again after every swap, and safe to call at any other time: it states the
    // whole of what the transcript should look like rather than the difference from anything.

    const paintMuted = () => {
      const box = transcript();
      if (!box) return;
      box.querySelectorAll(".panel").forEach((panel) => {
        if (muted.has(panel.dataset.kind)) panel.dataset.muted = "";
        else delete panel.dataset.muted;
      });
      document.querySelectorAll(".key__chip").forEach((chip) => {
        chip.setAttribute("aria-pressed", String(!muted.has(chip.dataset.kind)));
      });
    };

    // The shelf as it stands, rebuilt whole rather than diffed. It is a handful of rows the reader
    // is not interacting with mid-render, so the simplest correct thing is also the right one, and
    // it means every path that changes the shelf ends the same way.
    const paintShelf = () => {
      const list = document.querySelector('[data-shelf="list"]');
      const empty = document.querySelector('[data-shelf="empty"]');
      if (!list) return;
      if (empty) empty.hidden = shelf.length > 0;
      list.replaceChildren(
        ...shelf.map((slot, index) => {
          const row = document.createElement("li");
          row.className = "shelf__slot";
          const take = document.createElement("button");
          take.type = "button";
          take.className = "shelf__take";
          take.dataset.shelfTake = String(index);
          take.title = "Add this to the box";
          take.textContent = slot.name || "(blank)";
          const drop = document.createElement("button");
          drop.type = "button";
          drop.className = "shelf__drop";
          drop.dataset.shelfDrop = String(index);
          drop.title = "Take this off the shelf";
          drop.setAttribute("aria-label", `Take "${slot.name}" off the shelf`);
          drop.textContent = "×";
          row.append(take, drop);
          return row;
        }),
      );
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
      // by the server, and leaving that alone is what lets the server say so. A command reads the
      // same way and folds the same way, which is why one selector answers for both.
      box.querySelectorAll(FOLDS).forEach((fold) => {
        if (opened.has(fold.id)) fold.open = true;
      });
    };

    // What a panel says, as the one thing worth telling a reader has changed.
    //
    // The text of its blocks, and deliberately not its markup. A reader unfolding a call, a search
    // mark laid over a word, the kinds the key has switched off: all of those change a panel's
    // markup and none of them is news. Text changes when the model says something, when a call is
    // made, and when a result comes back, which while a turn is being answered is exactly what a
    // reader is watching for.
    //
    // Blocks rather than the whole panel, which leaves out the header row: an anchor and a role are
    // the same on every render, so nothing there is ever news.
    // Encoded rather than joined, so two blocks cannot be split differently and read the same: the
    // separator that would need is a character rendered text is not allowed to contain, and there
    // is no such character.
    const signature = (panel) =>
      JSON.stringify(Array.from(panel.querySelectorAll(":scope > .block"), (block) => block.textContent));

    // Worked out here rather than taken from the swap, because morphing reports nothing a listener
    // can hear: `htmx:before:morph:node` is an extension hook rather than a DOM event, and it fires
    // before htmx has decided whether the node differs at all.
    const paintFresh = (announce) => {
      const box = transcript();
      if (!box) return;
      const said = new Map();
      box.querySelectorAll(".panel").forEach((panel) => {
        const now = signature(panel);
        said.set(panel.id, now);
        if (!announce || signatures.get(panel.id) === now) return;
        // Cleared and re-set around a reflow, so a panel that changes twice in a row is marked
        // twice: re-adding an attribute an element already carries restarts no animation.
        delete panel.dataset.fresh;
        void panel.offsetWidth;
        panel.dataset.fresh = "";
      });
      signatures = said;
    };

    const paintFollow = () => {
      const toggle = document.querySelector('[data-follow="toggle"]');
      if (toggle) toggle.setAttribute("aria-pressed", String(following));
    };

    // --- The command box ---------------------------------------------------
    //
    // `!` in an empty box turns the composer into one, and Escape turns it back. It is a shortcut to
    // the `Run` row in the sending menu and never a second way of saying it: the server parses no
    // leader out of a message, so a paragraph that opens with `!` is a paragraph, and with this file
    // absent the menu is still there to be opened.
    //
    // One attribute is the whole of what this sets. Which button shows, what it is called, what it
    // posts and the sentence under the box are all in `pages.py` and drawn off `data-commanding` by
    // the stylesheet, so nothing here holds a label, a field name or a disposition. That is also what
    // makes the mode safe rather than the failure a remembered choice would be: the button a reader
    // is about to press is one the server rendered, saying what it does.
    const composerForm = () => document.querySelector(".composer[data-running]");

    const paintCommanding = () => {
      const form = composerForm();
      if (!form) return;
      if (commanding) form.dataset.commanding = "";
      else delete form.dataset.commanding;
    };

    // Which button a submit should be attributed to, which is whichever one the mode leaves standing.
    // `requestSubmit` with no submitter posts no name at all, so without this the keyboard would
    // always mean Send however the box was drawn.
    const submitter = (form) => form.querySelector(commanding ? ".sender__run" : ".sender__send");

    // --- Narrowing the branches -------------------------------------------
    //
    // The one field in the picker that is a search rather than a set of cards, because a starting
    // point is an open question: a branch, but also a tag, a hash, or `main~3`. So the branches are
    // drawn under the box and cut to what matches, and typing anything else is still typing.
    //
    // Everything here is an enhancement over a field that already works. With this file absent the
    // box keeps its `<datalist>` and the browser completes from the same names; what enhancing adds
    // is a list you can *see* and step through. Taking the `list` attribute off is the other half of
    // that: two dropdowns over one box is one more than a reader can use.

    const basisBox = () => document.querySelector(".basis__box");
    const basisFound = () => document.getElementById("branches-found");

    let matching = -1; // which of the shown branches the keyboard is on

    const paintBranches = () => {
      const box = basisBox();
      const found = basisFound();
      if (!box || !found) return;
      // Only where there is something to narrow. A repository that offered none leaves the field
      // exactly as it was, rather than declaring itself a combobox with an empty list behind it.
      if (!found.querySelector("[data-branch]")) return;
      box.removeAttribute("list");
      box.setAttribute("role", "combobox");
      box.setAttribute("aria-controls", found.id);
      box.setAttribute("aria-expanded", String(!found.hidden));
    };

    const shutBranches = () => {
      const found = basisFound();
      if (!found) return;
      matching = -1;
      found.hidden = true;
      basisBox()?.setAttribute("aria-expanded", "false");
    };

    // What is on offer for what is typed so far, as the buttons left showing. Case-insensitive and
    // anywhere in the name rather than a prefix, because a branch is named `feature/the-thing` far
    // more often than it is named for the word you remember about it.
    const narrowBranches = () => {
      const box = basisBox();
      const found = basisFound();
      if (!box || !found || box.getAttribute("role") !== "combobox") return [];
      const wanted = box.value.trim().toLowerCase();
      const showing = [];
      found.querySelectorAll("[data-branch]").forEach((one) => {
        const name = one.dataset.branch;
        // An exact match is the reader having already answered, so there is nothing left to offer:
        // a list holding only what is in the box is a menu whose one item changes nothing.
        const fits = name.toLowerCase().includes(wanted) && name !== box.value.trim();
        one.parentElement.hidden = !fits;
        if (fits) showing.push(one);
      });
      found.hidden = !showing.length;
      box.setAttribute("aria-expanded", String(!found.hidden));
      if (matching >= showing.length) matching = showing.length - 1;
      showing.forEach((one, at) => one.setAttribute("aria-selected", String(at === matching)));
      return showing;
    };

    const takeBranch = (name) => {
      const box = basisBox();
      if (!box) return;
      box.value = name;
      shutBranches();
      box.focus();
    };

    // --- Copying -----------------------------------------------------------
    //
    // A button on every panel, and one inside every block of code in it. Two places rather than two
    // things: one look, one listener, one clipboard, one way of saying it worked, and what each one
    // copies is decided by where it sits.
    //
    // Seated here rather than rendered by the server, which the code button forces: a fence is
    // markup the Markdown renderer produced, so there is no node for `pages.py` to hang a button on
    // inside one. Rendering the panel's and seating the code's would be two mechanisms for one
    // thing, and the seating has to exist either way.
    //
    // Named after where they sit - the panel, and where in it - so the confirmation below survives a
    // swap; see `paintCopied`. Positional within a panel, which is sound for the same reason a
    // panel's blocks are read that way: they only ever grow at the end.

    // `before` is where in its place the button goes, and `null` is the end: a block of code takes
    // one in its corner, and a panel takes one in the row of facts just ahead of the permalink,
    // which is also the order the stylesheet's own rules read in.
    const seated = (place, name, before = null) => {
      if (place.querySelector(":scope > [data-copy]")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy";
      button.dataset.copy = name;
      button.textContent = "copy";
      button.title = "Copy this to the clipboard";
      place.insertBefore(button, before);
    };

    const paintCopies = () => {
      const box = transcript();
      if (!box) return;
      box.querySelectorAll(".panel").forEach((panel) => {
        const meta = panel.querySelector(":scope > .panel__meta");
        // A panel with nothing in it yet is the one being waited on, and a button that would copy
        // the empty string is a control offering to do nothing.
        const says = [...panel.querySelectorAll(":scope > .block")].some((block) => block.textContent.trim());
        if (meta && says) seated(meta, panel.id, meta.querySelector(".panel__anchor"));
        // Only the code *inside a panel*: the raw record on a rule is a bounded box that scrolls,
        // and a button pinned in a scroller travels with the content and off its own corner.
        panel.querySelectorAll("pre").forEach((code, at) => seated(code, `${panel.id}:${at}`));
      });
    };

    // Taken off again before a swap, for the reason the search marks are: these are elements the
    // server has never heard of, and morphing merges incoming markup into what is on screen.
    const stripCopies = () => {
      const box = transcript();
      if (!box) return;
      box.querySelectorAll("[data-copy]").forEach((button) => button.remove());
    };

    // Which button has just been copied from, said on that button. A value projected rather than a
    // label left on the markup, for the reason everything else here is: while a turn is being
    // answered the transcript is morphed every time anything is recorded, so a button told it was
    // copied would be told otherwise a moment later - which is exactly when somebody is most likely
    // to be lifting a result out of a turn they are watching.
    const paintCopied = () => {
      const box = transcript();
      if (!box) return;
      box.querySelectorAll("[data-copy]").forEach((button) => {
        const done = button.dataset.copy === copied;
        button.toggleAttribute("data-copied", done);
        button.textContent = done ? "copied" : "copy";
      });
    };

    // --- Search ----------------------------------------------------------

    const clearHits = () => {
      const box = transcript();
      // Nothing marked and nothing asked for is the usual case while a turn is being answered, and
      // this runs on every render then: `normalize()` walks the whole conversation, so it is worth
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
        // A panel's own label and permalink are chrome, not conversation, and so is the word on a
        // copy button - which is seated inside a fence, where the marks would otherwise reach it.
        if (parent.closest(".panel__meta, [data-copy]")) continue;
        // Only the kinds the reader left in play, so the count is of what they are looking at.
        const panel = parent.closest(".panel");
        if (panel && muted.has(panel.dataset.kind)) continue;
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

    // `announce` is false exactly once, on the first render: every panel is new to this file then,
    // and a conversation that flashed itself top to bottom on being opened would be pointing at
    // everything, which is pointing at nothing.
    const repaint = (announce = true) => {
      paintFresh(announce);
      paintMuted();
      paintShelf();
      paintLanded();
      paintFolds();
      paintFollow();
      paintCommanding();
      paintBranches();
      paintCopies();
      paintCopied();
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
      paintFollow();
      landed = panel.id;
      paintLanded();
      try {
        history.replaceState(null, "", `#${panel.id}`);
      } catch {
        // A page served from somewhere `replaceState` refuses is still perfectly navigable.
      }
      // Through `scrolling`, so landing on the *last* panel does not put the reader at the end and
      // have the listener below read that as them asking to follow it again.
      scrolling(() => panel.scrollIntoView({ block: "start", behavior: "auto" }));
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
          if (muted.has(kind)) muted.delete(kind);
          else muted.add(kind);
          hold(scoped("muted"), JSON.stringify([...muted]));
          paintMuted();
          research(false);
        });
      });
    };

    // Keeping clears the box, which is what makes this "keep that" rather than "copy that": the
    // reason to shelve a paragraph is almost always to write a different one next.
    //
    // Taking *appends* to the box instead of replacing it, and never the other way round. It cannot
    // lose what somebody has already typed, and it is what assembles several kept comments into one
    // message, which is the case the shelf exists for.
    const wireShelf = () => {
      document.addEventListener("click", (event) => {
        const pressed = event.target;
        if (!(pressed instanceof HTMLElement)) return;
        const control = pressed.closest("[data-shelf], [data-shelf-take], [data-shelf-drop]");
        if (!(control instanceof HTMLElement)) return;
        const box = composerBox();

        if (control.dataset.shelf === "keep") {
          if (!box || !box.value.trim()) return;
          shelf = [...shelf, { name: labelled(box.value), text: box.value }];
          box.value = "";
          box.focus();
        } else if (control.dataset.shelfTake !== undefined) {
          const slot = shelf[Number(control.dataset.shelfTake)];
          if (!box || !slot) return;
          box.value = box.value.trim() ? `${box.value.replace(/\s+$/, "")}\n\n${slot.text}` : slot.text;
          box.focus();
          box.setSelectionRange(box.value.length, box.value.length);
          return; // Nothing was shelved or dropped, so there is nothing to store or redraw.
        } else if (control.dataset.shelfDrop !== undefined) {
          const index = Number(control.dataset.shelfDrop);
          shelf = shelf.filter((_, at) => at !== index);
        } else {
          return;
        }

        keepShelf();
        paintShelf();
      });
    };

    // A `<details>` closes only when its own summary is pressed again, which is right for a fold in
    // the transcript and wrong for a menu: a menu left open lies over the conversation until the
    // reader thinks to dismiss it the one way that works. Both of these are enhancements over a
    // control that already opens, chooses and submits with the file absent.
    const wireSender = () => {
      const shut = (except) => {
        document.querySelectorAll(".sender__more[open]").forEach((menu) => {
          if (menu !== except) menu.open = false;
        });
      };
      // Choosing an answer shuts the menu, whichever answer it was. The ones that send navigate or
      // swap so it hardly shows; `Keep` stays on the page, and a menu left standing over the box it
      // just emptied is the reader having to dismiss the thing they just used.
      //
      // Otherwise the summary's own press has already toggled by the time this runs, so the menu it
      // belongs to is spared and every other one shuts. A press that closed one leaves nothing open.
      document.addEventListener("click", (event) => {
        const within = event.target instanceof Element ? event.target.closest(".sender__more") : null;
        shut(event.target instanceof Element && event.target.closest(".sender__option") ? null : within);
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const open = document.querySelector(".sender__more[open]");
        if (!open) return;
        shut(null);
        open.querySelector("summary")?.focus();
      });
    };

    // Delegated for the reason `wireSend` is: this block is swapped in whenever a workspace card is
    // picked, so wiring the element at load would wire the one the page happened to start with.
    const wireBranches = () => {
      document.addEventListener("input", (event) => {
        if (event.target === basisBox()) narrowBranches();
      });
      // Opening on focus is what makes the list a way of *reading* what is on offer rather than only
      // of completing something already half typed.
      document.addEventListener("focusin", (event) => {
        if (event.target === basisBox()) narrowBranches();
        else if (!(event.target instanceof Element) || !event.target.closest(".basis__found")) shutBranches();
      });
      document.addEventListener("keydown", (event) => {
        const box = basisBox();
        if (event.target !== box || box.getAttribute("role") !== "combobox") return;
        if (event.key === "Escape") {
          const found = basisFound();
          if (found && !found.hidden) event.stopPropagation();
          shutBranches();
          return;
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          const showing = narrowBranches();
          if (!showing.length) return;
          // Preventing the default is what keeps the caret still: an arrow key in a text field
          // otherwise runs it to one end of what is typed while the selection moves behind it.
          event.preventDefault();
          matching = (matching + (event.key === "ArrowDown" ? 1 : -1) + showing.length) % showing.length;
          narrowBranches();
          showing[matching]?.scrollIntoView({ block: "nearest" });
          return;
        }
        if (event.key !== "Enter") return;
        const showing = narrowBranches();
        if (matching < 0 || !showing[matching]) return;
        // Only where the reader is actually on one. Enter in a text field submits its form, and this
        // field's form is the one that starts the session, so swallowing it whenever the list is
        // open would make the obvious key do nothing on a page whose whole purpose is that form.
        event.preventDefault();
        takeBranch(showing[matching].dataset.branch);
      });
      // `mousedown` rather than `click`, and that is the whole of why pressing one works: a click
      // takes the focus off the box first, and the `focusin` above would shut the list out from
      // under the press. Preventing the default here means the focus never leaves at all.
      document.addEventListener("mousedown", (event) => {
        const pressed = event.target instanceof Element ? event.target.closest("[data-branch]") : null;
        if (!pressed) {
          if (event.target instanceof Element && !event.target.closest(".basis__field")) shutBranches();
          return;
        }
        event.preventDefault();
        takeBranch(pressed.dataset.branch);
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
          const stops = stopsFor(button);
          const top = box.getBoundingClientRect().top;
          const tops = stops.map((stop) => stop.getBoundingClientRect().top - top);
          land(stops[stepIndex(tops, Number(button.dataset.step), 40)]);
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
          box.querySelectorAll(FOLDS).forEach((fold) => {
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
          paintFollow();
          if (following) toEnd();
        });
      }
    };

    // Following *is* being at the end, so where the reader has scrolled to decides it in both
    // directions: away from the end stops it, back to the end starts it again. One rule rather than
    // a release and a separate way back, which is what makes it a mode you can leave and return to
    // by doing the obvious thing, rather than a setting you have to remember you switched off.
    //
    // Watching the scroll itself covers the wheel, the keyboard and the scrollbar alike, which
    // three separate input listeners would not; `ours` is what keeps this file's own scrolls out of
    // it, and it has to, because every one of those would otherwise answer a question the reader
    // was not asked.
    const wireScroll = () => {
      document.addEventListener(
        "scroll",
        (event) => {
          const box = transcript();
          if (!box || event.target !== box || ours) return;
          const now = atEnd(box);
          if (now === following) return;
          following = now;
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

    // --- The picker ------------------------------------------------------
    //
    // Everything this file adds to the picker is an enhancement over markup that already works: the
    // fold is a checkbox and the folding is a CSS `:has()` rule, so with this absent a group opens,
    // collapses to what is checked, and posts it. What is added is the two conveniences that need a
    // script - shutting a group once something in it is picked, and narrowing an open one to what
    // is typed.
    //
    // A card is found structurally, as a label with a radio in it, rather than by a list of the
    // four classes that happen to be cards today, so a fifth kind of picker needs no edit here.

    const shut = (part) => {
      const toggle = part.querySelector(":scope > .picker__toggle");
      if (toggle) toggle.checked = false;
    };

    // Matching is over a card's whole text, which is why a model answers to its name and to the
    // routed id under it: both are printed on the card.
    const narrow = (part, needle) => {
      part.querySelectorAll("label:has(input[type=radio])").forEach((card) => {
        card.toggleAttribute("data-away", Boolean(needle) && !card.textContent.toLowerCase().includes(needle));
      });
      // A provider heading with nothing left under it is a heading for nothing.
      part.querySelectorAll(".models__provider").forEach((group) => {
        group.toggleAttribute("data-away", !group.querySelector("label:has(input[type=radio]):not([data-away])"));
      });
    };

    // Delegated, because the model group is replaced wholesale whenever the endpoint changes, so a
    // listener wired to the radios at load would be pointing at cards that no longer exist.
    const wireFolding = () => {
      document.addEventListener("change", (event) => {
        const pick = event.target;
        if (!(pick instanceof HTMLInputElement) || pick.type !== "radio") return;
        const part = pick.closest(".picker__part");
        if (part) shut(part);
      });
    };

    const wireFilter = () => {
      document.addEventListener("input", (event) => {
        const field = event.target;
        if (!(field instanceof HTMLInputElement) || !field.classList.contains("picker__filter-field")) return;
        const part = field.closest(".picker__part");
        if (!part) return;
        const needle = field.value.trim().toLowerCase();

        // Naming one exactly *is* choosing it, which is what taking an entry from the browser's
        // completion menu produces: leaving the reader to then reach for the single card still
        // showing is a step they have already taken. Only ever an exact match on the whole name,
        // so typing toward a longer one cannot pick something on the way past.
        const named = [...part.querySelectorAll("label[data-name]")].filter(
          (card) => card.dataset.name.toLowerCase() === needle,
        );
        if (named.length !== 1) {
          narrow(part, needle);
          return;
        }
        field.value = "";
        narrow(part, "");
        const pick = named[0].querySelector("input[type=radio]");
        if (pick && !pick.checked) {
          pick.checked = true;
          // A real `change`, so everything already listening hears it: the fold shuts, and an
          // endpoint's own `hx-get` swaps the model group. Setting `.checked` fires nothing.
          pick.dispatchEvent(new Event("change", { bubbles: true }));
        } else {
          // Already the choice, so nothing changes and no event would fire. Shut it anyway: naming
          // it is the reader saying they are done here either way.
          shut(part);
        }
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
        const box = event.target;
        if (!(box instanceof HTMLTextAreaElement) || box.name !== "prompt" || !box.form) return;
        // `!` into an *empty* box is what opens the command box, and only there: mid-message it is
        // an ordinary character, which is the whole reason the leader is a mode rather than
        // something the server strips off the front of what was posted.
        if (event.key === "!" && !commanding && box.value === "" && box.form.dataset.running !== undefined) {
          event.preventDefault();
          commanding = true;
          paintCommanding();
          return;
        }
        if (event.key === "Escape" && commanding) {
          event.preventDefault();
          commanding = false;
          paintCommanding();
          return;
        }
        if (event.key !== "Enter" || !event.shiftKey) return;
        event.preventDefault();
        // Named rather than left to the browser, because `requestSubmit()` with no submitter posts
        // no button's pair at all: unattributed, a command typed into a command box would arrive as
        // an ordinary message and be said to the model.
        box.form.requestSubmit(submitter(box.form));
      });
      // Sending is a decision to be looking at the end: whatever a reader had scrolled up to check
      // before typing, what they want to see now is the answer to what they just sent. On `submit`
      // rather than beside the keyboard path above, so the button, the keyboard, and anything else
      // that submits the form are one rule; `requestSubmit` is what makes that true of the keyboard,
      // since `submit()` would post without ever dispatching this.
      document.addEventListener(
        "submit",
        (event) => {
          const form = event.target;
          if (!(form instanceof HTMLFormElement)) return;
          const box = form.querySelector('textarea[name="prompt"]');
          if (!box) return;
          sentFrom = box;
          following = true;
          paintFollow();
        },
        true,
      );
      // And sending takes the focus off the box whichever way it was sent: the button takes it on a
      // click, and `hx-disable` blurs the box itself while the post is in flight. Either way the
      // next thing somebody does is type again, so the box is where the cursor belongs.
      //
      // A turn of the event loop later, because htmx dispatches this event and re-enables what it
      // disabled immediately afterwards: focused any sooner, the box is still disabled and takes
      // nothing. Only where nothing else has claimed the focus in the meantime, so a reader who went
      // to the search box while the message was in flight is left where they went.
      document.addEventListener("htmx:finally:request", () => {
        const box = sentFrom;
        sentFrom = null;
        if (!box) return;
        setTimeout(() => {
          const holding = document.activeElement;
          if (holding === null || holding === document.body) box.focus();
        }, 0);
      });
    };

    // --- What comes out of a copy button -----------------------------------

    // A node's text with this file's own buttons taken back out of it: a button seated inside a
    // fence is inside the very text that fence would otherwise hand over.
    //
    // `textContent` rather than `innerText`, and that is what makes the answer independent of what
    // the reader has open: `innerText` is what is *rendered*, so a folded call would copy as its
    // summary alone and one button would give two different answers a click apart.
    const wordsOf = (node) => {
      const taken = node.cloneNode(true);
      taken.querySelectorAll("[data-copy]").forEach((button) => button.remove());
      return taken.textContent;
    };

    // What one block says, as it was written rather than as it is drawn.
    //
    // A message is rendered Markdown, and the rendering is lossy in exactly the way somebody copying
    // cares about: the fences, the emphasis, the list markers and the table are gone from the text of
    // the page. So a block that was Markdown carries its own source and that is what is handed over -
    // see `written_block` in `pages.py`. A block of code inside one needs no such thing, since a
    // fence renders as the characters it was written with.
    //
    // A tool call is the block that is not simply its own text either: its parts are a name, what it
    // was handed and what it gave back, and run together they are one unreadable line. So the pairs
    // are read off the list the server already draws them as, which keeps the labels in one place -
    // "called with" and "returned" are written in `pages.py` and nowhere here.
    //
    // A command is the same problem in a smaller shape: the line, the status and the output run
    // together read as one word followed by a wall. Its line is what somebody copying almost always
    // wants back, so it leads, and its output follows on a line of its own.
    const spoken = (block) => {
      if (block.dataset.markdown !== undefined) return block.dataset.markdown.trim();
      const line = block.querySelector(".ran__line");
      if (line) {
        const output = block.querySelector(".ran__body");
        return [line.textContent.trim(), output && wordsOf(output).trim()].filter(Boolean).join("\n");
      }
      const body = block.querySelector(".tool__body");
      if (!body) return wordsOf(block).trim();
      const name = block.querySelector(".tool__name");
      const said = Array.from(body.children, (part) => wordsOf(part).trim());
      return [name && name.textContent.trim(), ...said].filter(Boolean).join("\n");
    };

    // A panel's blocks and nothing else: the role, the permalink and the button itself are chrome,
    // which is the same cut `signature` makes and the same one the search already skips.
    const copyable = (panel) =>
      Array.from(panel.querySelectorAll(":scope > .block"), spoken)
        .filter(Boolean)
        .join("\n\n");

    // The clipboard, or a throwaway textarea where the page has none to reach. That second path is
    // not a fallback around something that failed: `navigator.clipboard` is simply absent outside a
    // secure context, which a console reached at a bare address on a network is, and what it would
    // leave there instead is a button that silently does nothing.
    const copyToClipboard = async (text) => {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {}
      const box = document.createElement("textarea");
      box.value = text;
      box.style.position = "fixed";
      box.style.opacity = "0";
      document.body.appendChild(box);
      box.select();
      try {
        document.execCommand("copy");
      } catch {}
      box.remove();
    };

    // Delegated, because every one of these is seated inside the region that is morphed whenever the
    // session records anything: wired to the buttons themselves, the listeners would be pointing at
    // the panels of whatever the conversation looked like when it was opened.
    //
    // What a button copies is decided by where it sits: a block of code hands over itself, and a
    // panel's own hands over what the panel says.
    const wireCopy = () => {
      document.addEventListener("click", async (event) => {
        const pressed = event.target;
        if (!(pressed instanceof HTMLElement)) return;
        const button = pressed.closest("[data-copy]");
        if (!button) return;
        const code = button.parentElement.closest("pre");
        const panel = button.closest(".panel");
        if (!code && !panel) return;
        await copyToClipboard(code ? wordsOf(code) : copyable(panel));
        copied = button.dataset.copy;
        paintCopied();
        clearTimeout(saying);
        saying = setTimeout(() => {
          copied = null;
          paintCopied();
        }, 1200);
      });
    };

    // The mark comes off when the animation it drives has run, so a panel that changes again is
    // marked again. Named, because it is not the only animation on the page: the working dots run
    // forever, and clearing on any animation at all would take the mark off before it was seen.
    //
    // The animation is on the panel's `::after` and the event still arrives with the panel as its
    // target: an animation on a pseudo-element reports the element that originated it, and names
    // the pseudo separately.
    const wireFresh = () => {
      document.addEventListener(
        "animationend",
        (event) => {
          if (event.animationName !== "panel-arriving") return;
          if (event.target instanceof HTMLElement) delete event.target.dataset.fresh;
        },
        true,
      );
    };

    // --- Swaps -------------------------------------------------------------
    //
    // Everything this file put in the transcript comes out before the swap and goes back after it:
    // the search marks, and the copy buttons. Taking them off first is not tidiness: morphing merges
    // the incoming markup into the DOM already on screen, and elements this file put there are not
    // in that markup, so leaving them would make the merge reconcile nodes the server has never
    // heard of.
    const wireSwaps = () => {
      document.addEventListener("htmx:before:swap", (event) => {
        if (event.target !== transcript()) return;
        clearHits();
        stripCopies();
      });
      document.addEventListener("htmx:after:swap", () => repaint());
    };

    wireKey();
    wireShelf();
    wireSender();
    wireBranches();
    wireSearch();
    wireDock();
    wireScroll();
    wireFolds();
    wireTheme();
    wireClasp();
    wireFolding();
    wireFilter();
    wireSend();
    wireCopy();
    wireFresh();
    wireSwaps();
    wireHash();

    toCurrentSession();

    // A panel named in the URL is where the reader asked to be, and outranks following the end.
    const named = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    if (named) {
      following = false;
      landed = named;
    }
    repaint(false);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
