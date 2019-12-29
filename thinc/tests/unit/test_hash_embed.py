import numpy
from thinc.layers.hashembed import HashEmbed


def test_init():
    model = HashEmbed(64, 1000)
    assert model.get_dim("nV") == 1000
    assert model.get_dim("nO") == 64
    assert model.get_param("vectors").shape == (1000, 64)


def test_seed_changes_bucket():
    model1 = HashEmbed(64, 1000, seed=2)
    model2 = HashEmbed(64, 1000, seed=1)
    arr = numpy.ones((1,), dtype="uint64")
    vector1 = model1.predict(arr)
    vector2 = model2.predict(arr)
    assert vector1.sum() != vector2.sum()
