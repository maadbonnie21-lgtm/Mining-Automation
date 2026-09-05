from __future__ import annotations

from tools import p0_do_not_run_live


def test_no_live_sentinel_returns_stop(capsys) -> None:
    assert p0_do_not_run_live.main() == 2
    output = capsys.readouterr().out
    assert '"live_authorized": false' in output
    assert "software_startup_resolver_not_yet_offline_accepted" in output
