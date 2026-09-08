"""The asset graph's shape. No network — this is structure, not execution."""

from __future__ import annotations

from ndip.orchestration.definitions import defs


def parents() -> dict[str, set[str]]:
    graph = defs.resolve_asset_graph()
    return {
        key.to_user_string(): {p.to_user_string() for p in graph.get(key).parent_keys}
        for key in graph.get_all_asset_keys()
    }


def test_every_stage_of_the_pipeline_is_an_asset():
    assert set(parents()) == {
        "bronze_observations",
        "silver_change_polygons",
        "gold_exposure",
        "gold_event_context",
        "report_page",
    }


def test_layer_order_is_declared_rather_than_remembered():
    """The reason for the graph: nothing has to recall that detect runs after ingest."""
    p = parents()
    assert p["bronze_observations"] == set()
    assert p["silver_change_polygons"] == {"bronze_observations"}


def test_both_gold_tables_hang_off_silver_and_not_off_each_other():
    """They are independent, so they can run at the same time."""
    p = parents()
    assert p["gold_exposure"] == {"silver_change_polygons"}
    assert p["gold_event_context"] == {"silver_change_polygons"}


def test_the_report_reads_both_gold_tables_and_nothing_earlier():
    """The page must not reach past gold. Anything it re-derives from silver would be
    a second implementation of a rule that already has one."""
    assert parents()["report_page"] == {"gold_exposure", "gold_event_context"}


def test_only_the_first_asset_reaches_outside_the_lakehouse():
    """Downstream assets read tables. If one starts calling a remote catalogue the
    dependency it declares stops being the thing it actually depends on."""
    import inspect

    from ndip.orchestration import definitions

    for name in ("silver_change_polygons", "gold_exposure", "gold_event_context", "report_page"):
        body = inspect.getsource(getattr(definitions, name).op.compute_fn.decorated_fn)
        assert "StacSearch" not in body, f"{name} re-queries the STAC catalogue"
        assert "discover(" not in body, f"{name} re-runs discovery"
