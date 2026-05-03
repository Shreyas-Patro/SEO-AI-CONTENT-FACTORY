"""
Topic queue scheduler.

Picks up queued topics, fires off layer1→2→3 in a worker process,
respects budget cap (max concurrent + max $/day).
"""
import threading
import time
from db.sqlite_ops import list_topic_queue, update_topic_queue, db_conn
from db.artifacts import list_pipeline_runs, update_pipeline_run
from orchestrator import start_pipeline_run, run_layer1, run_layer2, run_layer3
from jobs import job_manager
from config_loader import cfg

MAX_CONCURRENT = 3              # tune to your machine
DAILY_BUDGET_USD = cfg.get("budget", {}).get("max_llm_dollars_per_topic", 30.0) * 5


def _today_spend() -> float:
    """Sum cost of pipeline_runs created today."""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    runs = list_pipeline_runs(limit=200)
    return sum(
        r.get("total_cost_usd", 0) or 0
        for r in runs
        if (r.get("created_at") or "").startswith(today)
    )


def _running_topics() -> int:
    queued = list_topic_queue(limit=200)
    return sum(1 for q in queued if q["status"] == "running")


def _process_topic(qid: str, topic: str):
    """Run a single topic through all 3 layers + auto-approve gate."""
    try:
        run_id = start_pipeline_run(topic)
        update_topic_queue(qid, status="running", run_id=run_id, started_at=__import__("datetime").datetime.utcnow().isoformat())

        run_layer1(run_id)
        # Auto-approve gate IF running unattended (queue mode).
        # For supervised runs, users use the manual gate via dashboard.
        from orchestrator import approve_gate
        approve_gate(run_id)

        run_layer2(run_id)
        run_layer3(run_id)

        from db.artifacts import get_pipeline_run
        run = get_pipeline_run(run_id)
        update_topic_queue(
            qid, status="done",
            completed_at=__import__("datetime").datetime.utcnow().isoformat(),
            cost_usd=run.get("total_cost_usd", 0),
        )
    except Exception as e:
        import traceback
        print(f"[scheduler] topic {topic} failed: {e}\n{traceback.format_exc()}")
        update_topic_queue(qid, status="failed")


def scheduler_loop(stop_event: threading.Event):
    print("[scheduler] starting loop")
    while not stop_event.is_set():
        try:
            running = _running_topics()
            if running >= MAX_CONCURRENT:
                time.sleep(5)
                continue

            today_spend = _today_spend()
            if today_spend >= DAILY_BUDGET_USD:
                print(f"[scheduler] daily budget hit (${today_spend:.2f}/${DAILY_BUDGET_USD}), pausing 60s")
                time.sleep(60)
                continue

            queued = [q for q in list_topic_queue(limit=200) if q["status"] == "queued"]
            if not queued:
                time.sleep(5)
                continue

            slots = MAX_CONCURRENT - running
            for q in queued[:slots]:
                job_manager.submit(
                    f"topic-{q['id']}",
                    _process_topic, q["id"], q["topic"],
                )
                print(f"[scheduler] dispatched {q['topic']!r} (qid={q['id']})")
        except Exception as e:
            print(f"[scheduler] loop error: {e}")
        time.sleep(3)


# Bootstrap on app startup
_scheduler_thread = None
_stop_event = threading.Event()


def start_scheduler():
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=scheduler_loop, args=(_stop_event,), daemon=True,
    )
    _scheduler_thread.start()


def stop_scheduler():
    _stop_event.set()