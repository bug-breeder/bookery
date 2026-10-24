"""Interactive lessons built on top of the reconciled chapter model.

The fidelity pipeline (stages 0-4) proves the site lost nothing. This package
goes the other way on purpose: it condenses a chapter into a short, visual,
interactive lesson -- but keeps the same discipline about what can be
asserted. Claude authors `lessons/chNN/lesson.json`; everything else here is
deterministic: the source pack Claude reads, the gates that check what it
wrote against the book, and the build that turns it into a single-file
Artifact page.
"""
