# Deploying the console

The target is a public URL a judge can open, at no cost, without republishing
the organisers' dataset.

**Shape.** A Hugging Face Space builds `deploy/Dockerfile`, which clones this
repository. At boot, `deploy/boot.py` pulls a ~20 MB data bundle from a
**private** Hugging Face dataset repo and starts the console on port 7860.

**Why the split.** The console can serve the 20 published answer files with
nothing behind them, but a live investigation needs the transaction cache —
and that cache is the challenge dataset in another form. It goes in a private
repo, not the public image. Without it the console still runs and says the
graph is unavailable, on every screen.

**What it costs.** Nothing. Spaces' free CPU tier is 2 vCPU and 16 GB RAM,
with no card. The console holds about 450 MB once the mirror is loaded, which
is why the free 512 MB tiers elsewhere are not enough.

---

## 1. Build the bundle

```bash
python scripts/make_deploy_bundle.py
```

Writes `build/deploy_bundle/` (~20 MB): the transaction index, the entity
tables, the closed cases, the case records the console renders, the model
cards and the agent journal. It does not include the 675 MB source CSVs.

## 2. Put it in a private repo

1. <https://huggingface.co/new-dataset> → name it `fraudgraph-data` → set
   **Private** → Create.
2. Open the **Files** tab → *Add file* → *Upload files* → drag in everything
   inside `build/deploy_bundle/` (including the `case_records` folder) →
   Commit.

## 3. Make a read token

<https://huggingface.co/settings/tokens> → **Create new token** → type
**Read** → give it access to that dataset → copy it. You will paste it into
the Space's secrets in step 5; it is never committed anywhere.

## 4. Create the Space

1. <https://huggingface.co/new-space> → name `fraud-console` → **Docker** →
   *Blank* → **Public** → Create.
2. That gives you an empty git repo. Put two files in it:

```bash
git clone https://huggingface.co/spaces/<you>/fraud-console space
cp deploy/Dockerfile space/Dockerfile
cp deploy/space/README.md space/README.md
cd space && git add -A && git commit -m "Fraud investigation console" && git push
```

Git will ask for your Hugging Face username and the token as the password.

If your GitHub repository is not `AKRai-2005/fraudgraph`, edit the `REPO`
build argument at the bottom of the Dockerfile before committing.

## 5. Set the secrets

Space → **Settings** → *Variables and secrets*:

| Name | Kind | Value |
|---|---|---|
| `FG_DATA_REPO` | Variable | `<you>/fraudgraph-data` |
| `HF_TOKEN` | Secret | the read token from step 3 |
| `TG_HOST` | Secret | your Savanna URL — optional |
| `TG_SECRET` | Secret | your Savanna secret — optional |

The Space rebuilds. Watch **Logs**; `[boot]` lines report what was fetched,
and a failure says so and serves the degraded console rather than dying.

**Leave `GEMINI_API_KEY` unset.** The free tier allows 20 requests a day and
each live investigation spends two, so a handful of visitors would exhaust it
and the console would narrate from templates anyway. Unset, it says `LLM off`
honestly. Set it only while recording the demo.

## What works, and when

| | Without TigerGraph | With the workspace awake |
|---|---|---|
| The 20 cases, evidence, policy, approvals, backtest | yes | yes |
| Graph view, live re-run, ad-hoc investigation | yes, on the local mirror | yes, on TigerGraph |
| A case written back into the graph | no — and the console says so | yes |

Approvals and re-runs made by visitors are simulated and ephemeral: the Space
resets to the published answers whenever it restarts.

## Updating it

The image clones the repository at build time, so a new commit on `main`
reaches the Space only on a rebuild: **Settings → Factory rebuild**. Changing
data means re-uploading the bundle and restarting.

## Other hosts

Nothing here is Spaces-specific except the port and the README front matter.
Any host that runs a container works: build `deploy/Dockerfile`, set the same
environment, expose 7860. On a host with less than 1 GB of RAM, leave
`FG_DATA_REPO` unset and point `TG_HOST`/`TG_SECRET` at TigerGraph instead —
the mirror is what needs the memory.
