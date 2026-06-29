"""Diagnostic / accuracy-report utilities.

Read-only walkers that exercise the parser + resolver pipeline against
a source tree and report per-file matching state. Intended as the
iteration loop for refining grouping and TMDB ranking — the report
exposes top candidate + alternatives + diagnostic flags so the user
can spot-check accuracy at scale without ever copying or moving a
file.

Everything in this package is read-only: no journal writes, no apply,
no settings mutation.
"""
