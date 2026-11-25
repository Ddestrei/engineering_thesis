import pytest

from anomaly.generator.dataset import build_dataset


@pytest.fixture(scope="session")
def dataset11():
    """Pełny zbiór dla ziarna 11 (D6), generowany raz na sesję testów."""
    users, splits = build_dataset(11)
    return users, splits
