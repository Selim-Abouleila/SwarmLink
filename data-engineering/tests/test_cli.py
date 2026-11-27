import json
import sys

from swarmlink_data.__main__ import main


def test_replay_filters_robot_and_counts_malformed_journal_records(tmp_path, event, monkeypatch, capsys):
    chosen = event()
    records = [json.dumps(chosen), json.dumps(event(robot_id="robot-02")),
               json.dumps({"raw_json": "{broken"}), "null", "{torn",
               json.dumps({"raw_json": json.dumps(chosen)})]
    path = tmp_path / "receipts.jsonl"
    path.write_text("\n".join(records) + "\n")
    calls = []
    def send(endpoint, robot, token, events):
        calls.extend(events)
        return [{"event_id": events[0]["event_id"], "status": "duplicate"}]
    monkeypatch.setattr("swarmlink_data.edge.send_batch", send)
    monkeypatch.setattr(sys, "argv", ["swarmlink-data", "replay", str(path), "--robot-id", "robot-01"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result == {"accepted": 0, "duplicate": 2, "rejected": 0, "skipped": 1, "malformed": 3}
    assert calls == [chosen, chosen]
