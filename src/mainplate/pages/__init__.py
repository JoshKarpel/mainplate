# Every page and fragment the console renders, as node trees, one module per part of the page.
#
# Pure functions of already-answered questions: nothing here reads a store, and nothing here
# knows what a `Run` is. `console` gathers what a page needs and calls one of these, which is
# what lets a page and the fragment inside it be the *same* function called at two depths rather
# than two renderings of one thing that can disagree.
#
# Nothing is re-exported from here. A caller imports from the module a name lives in, so every
# import says where to look and there is no second list of names to keep in step with the modules.
# `AGENTS.md` beside this says which module a new piece belongs in.
