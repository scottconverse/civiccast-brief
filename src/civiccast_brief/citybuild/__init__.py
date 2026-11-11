"""Setting up a new city without a model: search its meeting sources, then check them.

``search`` reads the city's own website (or the one it guesses), the pages the editor adds, and
the public lists of the meeting platforms (PrimeGov, Legistar), and says which sources and bodies
it found. ``build`` writes them into the city folder as stubs for the editor to confirm.
``read`` checks one source by reading one real recent meeting from it.

Nothing here calls a model. Every step is a web request and a rule a person can read.
"""
