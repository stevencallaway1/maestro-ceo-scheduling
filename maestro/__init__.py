"""Maestro: an inspectable AI scheduling system for the Office of the CEO.

A six-stage request pipeline - intake, context, policy, planner, critic,
approval - orchestrated by :class:`maestro.pipeline.Pipeline`, plus a
deterministic calendar adapter and three supporting services (calendar
optimizer, daily brief, eval loop feeding the trust ladder).

Only the planner and the critic call a model, once each. Every decision ships
with a context dossier and a written rationale, every action is audited, and
autonomy is earned one trust-ladder level at a time.
"""
