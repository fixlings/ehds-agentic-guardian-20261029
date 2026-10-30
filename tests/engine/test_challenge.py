from ehds.evaluate import main


def test_all_five_metrics():
    assert main() == 0
