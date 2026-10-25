from __future__ import annotations

import copy
import pickle

from yaml_compose import UNSET, UnsetType


def test_unset_has_one_shared_instance() -> None:
    assert UnsetType() is UNSET


def test_copying_unset_preserves_its_identity() -> None:
    assert copy.copy(UNSET) is UNSET
    assert copy.deepcopy(UNSET) is UNSET


def test_pickling_unset_preserves_its_identity() -> None:
    assert pickle.loads(pickle.dumps(UNSET)) is UNSET
