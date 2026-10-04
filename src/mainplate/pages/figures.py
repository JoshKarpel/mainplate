# Numbers as the page words them: tokens, bytes on disk, dollars, and how much of a context window
# a request used. Words and fractions rather than elements, so a rule, a card and a note that print
# the same figure print it the same way.

from __future__ import annotations

from decimal import Decimal

from mainplate.pages.moments import Reader
from mainplate.pages.moments import timed
from mainplate.sessions import Footprint


def tokens(count: int) -> str:
    """
    A token count as a person compares them, which is to two or three figures and no more.

    Decimal throughout, because the two wires disagree about the base and a card mixing them would
    be the one place on the page where 256K and 262K meant the same thing. A model's context is a
    number to weigh against another model's, not a size to allocate against.
    """
    if count >= 1_000_000:
        # One decimal, and not even that when it would be a zero. A context window is a number to
        # weigh against another model's, so the digits past the first are noise: a 2^20 window is
        # `1.04858M` at full precision, which reads as precision nobody asked for about a figure
        # that is round in the other base.
        return f"{f'{count / 1_000_000:.1f}'.removesuffix('.0')}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}K"
    return str(count)


def sized(allocated: int) -> str:
    """
    Bytes on disk or in memory as a person compares them, which is to two or three figures in a
    binary unit.

    Binary, unlike `tokens`, because this is a size to allocate against rather than a number to
    weigh: it is what `du` prints and what `df` will get back, and what a process's memory is
    budgeted in, and the unit says which base it is in so nobody has to know. One decimal under ten
    and none above, since `1.2 GiB` and `466 MiB` are each the digits a person reads and `1.21 GiB`
    is precision nobody asked for.
    """
    value = float(allocated)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            break
        value /= 1024
    figure = str(round(value)) if value >= 10 or unit == "B" else f"{value:.1f}".removesuffix(".0")
    return f"{figure} {unit}"


def footprint_note(footprint: Footprint, reader: Reader) -> str:
    """
    What the figure on a row is a figure of, and when it was true, as the sentence behind it.

    One sentence for both places the figure is drawn, so the sidebar and the session's card in the
    rail cannot describe the same directories two ways. It says *when* because the number is as old
    as the last sweep, and a size with no time beside it reads as current.
    """
    return (
        f"{sized(footprint.allocated)} on disk across this session's checkout, scratch and plugins, "
        f"measured at {timed(footprint.measured_at, reader)}"
    )


def dollars(rate: float) -> str:
    """
    A price per million tokens, carrying the digits that vary and no others.

    Significant figures rather than a fixed two decimals, because these rates span four orders of
    magnitude: a fixed width writes the cheap end as `$0.08` where the difference between models is
    in the next digit, and pads the expensive end to `$75.00` where the cents are noise. It also
    keeps a pair consistent with itself - `$5.00/$25` reads as two differently-measured numbers,
    where `$5/$25` reads as the ratio it is.
    """
    return "free" if rate == 0 else f"${rate:g}"


def charged(cost: Decimal) -> str:
    """
    What a turn or a session came to, at the precision a person actually reads.

    Four decimal places is the floor rather than the format, because these figures span from a
    fraction of a cent to tens of dollars and no single width serves both: a fixed two decimals
    writes most single turns as `$0.00`, and full precision writes a long session as fourteen
    digits of a number nobody is going to check to the picogram. Below the floor the figure is
    named as being under it, which is a true thing to say where `$0.00` is not.

    Zero is `free` and not `$0`, matching what a card says about a model that charges nothing: the
    two are the same claim and reading them differently on one page would suggest they are not.
    """
    if cost == 0:
        return "free"
    if cost >= 1:
        return f"${cost:.2f}"
    if cost < Decimal("0.0001"):
        return "<$0.0001"
    return f"${cost.quantize(Decimal('0.0001')):f}".rstrip("0").rstrip(".")


def consumed(context: int, window: int | None) -> float | None:
    """
    How much of a model's context window this much context takes up, as a fraction of it.

    Nothing at all where either half is missing, which is one answer to three questions - no
    reference database, an endpoint that no longer lists the recorded id, a model nobody wrote a
    window down for - because the page does the same thing with all three.

    Uncapped, deliberately. A window is what a database says and the count is what a provider
    reported, so the two can disagree and a session past 100% is a real state worth seeing said
    rather than a figure to round back down to full. What is capped is the *gauge*, which cannot
    draw past its own width.
    """
    if not context or not window:
        return None
    return context / window


def along(fraction: float) -> str:
    """
    One fraction as a distance along a rule, capped because a gauge cannot draw past its own width.

    A session past a window the database understates asks for no more line than there is, which is
    the one way a fraction here can exceed one.
    """
    return f"{min(fraction, 1.0):.1%}"


def portion(fraction: float) -> str:
    """
    A fraction as the whole numbers of percent a person reads it in.

    Named as being under one rather than shown as `0%`, which is `charged`'s rule about `<$0.0001`
    at the other end of the same problem: a long conversation on a very large window really is a
    fraction of a percent of it, and rounding that to nothing says the window is untouched.
    """
    if fraction < 0.01:
        return "<1%"
    return f"{fraction:.0%}"


def counted(many: int, thing: str, plural: str | None = None) -> str:
    """
    `3 models`, `1 option`, `2 branches`: a count with the word it counts, pluralised.

    The plural is given where an `s` does not make one, and asked for rather than worked out: an
    English pluraliser is a pile of rules and exceptions to get a handful of nouns right, where the
    caller already knows the word it is passing.
    """
    return f"{many} {thing if many == 1 else plural or f'{thing}s'}"
