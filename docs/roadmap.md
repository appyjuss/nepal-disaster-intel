# Roadmap

**Status:** written 2026-09-15, after V1 shipped. Supersedes the one-line version ladder at the
foot of [`contract-v1.md`](contract-v1.md).

Nothing below is built. Each entry says what it is, why it comes when it does, and what would
have to be true to call it done. V1's scope freeze is over; this is what comes next and in what
order.

## Where V1 left things

V1 reconstructs one event, end to end, on a laptop. Five Iceberg tables rebuild from empty on one
command. The warehouse runs against a local directory or S3-compatible object storage on one
environment variable, and that has been verified by hand against MinIO.

Two consequences shape everything here. **The code is cloud-ready but has never run in a cloud** —
the S3 path is exercised manually, never in CI, and against MinIO rather than AWS. And **there is
no infrastructure-as-code**: `infra/` is an empty directory skeleton, and `mise.toml` pins
opentofu for a `tf-test` task that would fail today.

## Track A — make the deployment claim true

Small, unblocked, and it closes the gap between what the code is designed for and what has been
demonstrated. This track does not appear in the old version ladder; it should come before V2.

| | What done looks like | Depends on |
|---|---|---|
| **A1 · Terraform object store** | A reusable module provisions the warehouse bucket with policy, versioning and lifecycle rules in version control. `tf-test` validates the plan against mocked providers and passes. | nothing |
| **A2 · Run against real S3** | The cold rebuild runs against an AWS S3 bucket, not MinIO, and produces a row count identical to the local-disk run. The comparison is a test, not a manual note. | A1 |
| **A3 · Metrics export** | Dagster run outcomes, durations and failures reach Prometheus and render in Grafana, so a slow or failed materialization is visible without opening a run log. | nothing |
| **A4 · Kubernetes deploy** | Object store, Iceberg catalog, and the Dagster daemon and webserver run in-cluster, each pipeline stage an independently scalable job against the same warehouse URI the local stack uses. | A1, A3 |

A1 and A2 are the ones worth doing first. They convert "moving to the cloud is a URI and
credential change rather than a rewrite" from a design assertion into a result, and that sentence
is the most load-bearing claim the project makes.

## Track B — scale and capability

The original version ladder, expanded. Each rung assumes the one before it.

| | What it is | Why it waits |
|---|---|---|
| **V2 · Nepal-wide on Spark** | Lift the AOI from one 0.6° × 0.8° corridor to the country. DuckDB stops being the right engine somewhere below that; Spark or equivalent takes over the silver and gold builds. | Needs A1–A2. Running nationwide from a laptop is not the point; running it on rented compute is. |
| **V3 · Historical disaster corpus** | Replay the same pipeline over past events to build a labelled archive rather than a single case study. | Needs V2. One event at a time on local compute does not produce a corpus. |
| **V4 · ML susceptibility and impact** | Move from reconstruction to prediction: model where change is likely given terrain, drainage and rainfall. | Needs V3. There is nothing to train on until the corpus exists. |
| **V5 · GenAI analyst layer** | Natural-language questions answered from gold, with citations back to the rows that support them. | Needs V4, or at least V3. An analyst layer over one event is a demo, not a capability. |
| **V6 · Continuous ingest** | Scheduled rather than invoked: new scenes land, the graph materializes, changes surface without a human running a command. | Needs A3–A4. Scheduling something with no metrics export is how you get a job that succeeds having done nothing. |

## Ordering

A1 → A2 first, then A3. V2 unblocks the whole of Track B and depends on A1–A2, so it comes after.
A4 and V6 pair naturally and can wait.

The V1 scope freeze deliberately avoided decisions that would have made Track A harder: no
engine-specific SQL in the silver and gold builds, one module reading the environment, and row ids
derived from content so a rerun in a new place corrects rather than duplicates.

## Not planned

No web UI. No real-time or near-real-time alerting. No commercial or licence-restricted data
sources — the project runs on open data by design, and that constraint is load-bearing rather than
a limitation to be removed later.
