from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.ui.workflows.secondary import (
    FILE_SORT_LABELS,
    SecondaryUI,
    file_sort_state,
)


def state_rows(selected="newest"):
    return [(key, key == selected, True) for key in FILE_SORT_LABELS]


def test_native_sort_state_requires_one_unambiguous_checked_option():
    assert file_sort_state("12345", state_rows()).current == "newest"
    assert (
        file_sort_state("12345", [(k, True, True) for k in FILE_SORT_LABELS]).current
        is None
    )
    assert (
        file_sort_state("12345", [(k, None, True) for k in FILE_SORT_LABELS]).current
        is None
    )
    assert file_sort_state("12345", state_rows()[:2]).current is None


@pytest.mark.parametrize("selection_fails", [False, True])
def test_temporary_sort_restores_original_even_when_selection_or_read_fails(
    selection_fails,
):
    ui = SecondaryUI.__new__(SecondaryUI)
    calls = []
    ui._sort_state = lambda _: SimpleNamespace(current="newest")

    def set_order(group, order):
        calls.append((group, order))
        if selection_fails and order == "size_asc":
            raise RuntimeError("Provider disappeared during selection")

    ui._set_file_order = set_order
    with pytest.raises(RuntimeError), ui.temporary_file_order("12345", "size_asc"):
        raise RuntimeError("Provider disappeared during read")
    assert calls == [("12345", "size_asc"), ("12345", "newest")]


def test_unknown_original_sort_blocks_selection_before_any_view_change():
    ui = SecondaryUI.__new__(SecondaryUI)
    ui._sort_state = lambda _: SimpleNamespace(current=None)
    ui._set_file_order = lambda *_: pytest.fail(
        "Must not select without restorable original state"
    )
    with pytest.raises(ControlUnavailable), ui.temporary_file_order("12345", "oldest"):
        pytest.fail("Must not read in an unverified order")


@pytest.mark.parametrize(("query", "order"), [(None, "other"), ("search", "oldest")])
def test_invalid_file_orders_fail_before_accessing_a_window(query, order):
    ui = SecondaryUI.__new__(SecondaryUI)
    with pytest.raises(ValueError):
        ui.group_files("12345", query=query, order=order)


def test_linux_time_sort_menu_has_an_explicit_complete_option_set():
    state = file_sort_state("12345", state_rows()[:2], expected_keys=("newest", "oldest"))
    assert state.current == "newest"
    assert file_sort_state("12345", state_rows()[:1], expected_keys=("newest", "oldest")).current is None
