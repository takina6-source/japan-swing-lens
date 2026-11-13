import json
import sys

import pytest

from engine.state_io import StateError
from scripts import verify_state_publication as publication


@pytest.mark.parametrize('failures', [10, 40])
def test_publication_waits_for_propagation_but_never_ignores_mismatch(tmp_path, monkeypatch, failures):
    ledger = tmp_path / 'generation.json'
    ledger.write_text(json.dumps({'run_id': 100}))
    monkeypatch.setattr(sys, 'argv', ['verify', '--root-url', 'https://test', '--ledger', str(ledger)])
    calls, sleeps = [], []

    def verify(root, actual):
        assert root == 'https://test' and actual == {'run_id': 100}
        calls.append(actual)
        if len(calls) <= failures:
            raise StateError('Pages differs from latest independent generation')

    monkeypatch.setattr(publication, 'verify', verify)
    monkeypatch.setattr(publication.time, 'sleep', sleeps.append)
    if failures == 40:
        with pytest.raises(StateError, match='Pages differs'):
            publication.main()
        assert len(calls) == 40
    else:
        publication.main()
        assert len(calls) == 11
    assert sleeps == [15] * (len(calls) - 1)
