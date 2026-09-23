---
title: Fraud Investigation Console
emoji: 🔍
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: An agent that investigates fraud alerts against a TigerGraph knowledge graph
---

# Fraud investigation console

An investigation agent for the TigerGraph × Hacker House Goa challenge. It
takes a fraud alert, investigates it against a knowledge graph of 590,742
transactions, works out what kind of fraud it is — if any — how far it goes,
and what the bank should do next under Fraud Policy v1.0.

Source: <https://github.com/AKRai-2005/fraudgraph>

**Everything here is simulated.** No action reaches a real financial system;
`L1` and `L2` actions wait for a named approver and record a simulated
execution. The status strip names which graph answered each query.

This Space builds the image in `deploy/Dockerfile` from the repository above
and pulls its data at boot from a private repository — the dataset belongs to
the organisers and is not republished here. Without it, the console still
serves the 20 published answer files and says the graph is unavailable.
