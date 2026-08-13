from __future__ import annotations

import numpy as np
import pytest

import turbovec_adapter
from turbovec_adapter import TurboVecIndex, stable_vector_id


@pytest.mark.skipif(
    not turbovec_adapter._HAS_TURBOVEC,
    reason="incremental persistence requires installed turbovec",
)
def test_loaded_index_increment_preserves_existing_vectors(tmp_path):
    vector_dir = tmp_path / "vectors"
    index = TurboVecIndex(
        "test",
        dim=8,
        root=tmp_path,
        vector_dir=vector_dir,
    )
    index.insert("existing", np.asarray([1.0, 0, 0, 0, 0, 0, 0, 0]))
    index.insert("second", np.asarray([0, 1.0, 0, 0, 0, 0, 0, 0]))
    index.save()

    loaded = TurboVecIndex(
        "test",
        dim=8,
        root=tmp_path,
        vector_dir=vector_dir,
    )
    assert loaded.count() == 2
    loaded.insert("new", np.asarray([0, 0, 1.0, 0, 0, 0, 0, 0]))
    loaded.save()

    reloaded = TurboVecIndex(
        "test",
        dim=8,
        root=tmp_path,
        vector_dir=vector_dir,
    )
    assert reloaded.count() == 3
    assert reloaded._index.contains(stable_vector_id("existing", "test"))
    assert reloaded._index.contains(stable_vector_id("second", "test"))
    assert reloaded._index.contains(stable_vector_id("new", "test"))


@pytest.mark.skipif(
    not turbovec_adapter._HAS_TURBOVEC,
    reason="incremental persistence requires installed turbovec",
)
def test_loaded_index_replaces_existing_id_without_growing_count(tmp_path):
    vector_dir = tmp_path / "vectors"
    index = TurboVecIndex(
        "test",
        dim=8,
        root=tmp_path,
        vector_dir=vector_dir,
    )
    index.insert("existing", np.asarray([1.0, 0, 0, 0, 0, 0, 0, 0]))
    index.save()

    loaded = TurboVecIndex(
        "test",
        dim=8,
        root=tmp_path,
        vector_dir=vector_dir,
    )
    loaded.insert("existing", np.asarray([0, 1.0, 0, 0, 0, 0, 0, 0]))
    loaded.save()

    assert loaded.count() == 1
    result = loaded.search(np.asarray([0, 1.0, 0, 0, 0, 0, 0, 0]), 1)
    assert result[0]["vector_id"] == stable_vector_id("existing", "test")
