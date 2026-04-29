from agents.s08_background_tasks import BackgroundManager, inject_background_results


def test_background_manager_runs_command_and_queues_notification(tmp_path) -> None:
    bg = BackgroundManager(tmp_path)
    start_message = bg.run("python3 -c \"print('done')\"")

    assert "Background task" in start_message

    bg.join_all(timeout=5)
    status_text = bg.check()
    notifications = bg.drain_notifications()

    assert "[completed]" in status_text
    assert notifications
    assert notifications[0]["status"] == "completed"
    assert "done" in notifications[0]["result"]
    assert bg.drain_notifications() == []


def test_inject_background_results_appends_user_message(tmp_path) -> None:
    bg = BackgroundManager(tmp_path)
    bg.run("python3 -c \"print('background hello')\"")
    bg.join_all(timeout=5)

    original_bg = inject_background_results.__globals__["BG"]
    inject_background_results.__globals__["BG"] = bg
    try:
        messages = [{"role": "user", "content": "start"}]
        inject_background_results(messages)
    finally:
        inject_background_results.__globals__["BG"] = original_bg

    assert len(messages) == 2
    assert messages[-1]["role"] == "user"
    assert "<background-results>" in messages[-1]["content"]
    assert "background hello" in messages[-1]["content"]
