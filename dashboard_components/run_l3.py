"""
Run Layer 3 from the command line for a specific run.

Usage:
    python run_l3.py                       # lists recent runs
    python run_l3.py prun-XXXXXXXX         # runs L3 for that run
    python run_l3.py prun-XXXXXXXX --reset # resets stage first, then runs L3
"""
import sys
from pathlib import Path

from db.artifacts import (
    list_pipeline_runs, get_pipeline_run, update_pipeline_run,
)


def list_runs():
    runs = list_pipeline_runs(limit=10)
    if not runs:
        print("No runs found.")
        return
    print(f"\n{'ID':<40} {'TOPIC':<20} {'STATUS':<12} {'STAGE':<22} {'CLUSTER':<14}")
    print("-" * 110)
    for r in runs:
        print(
            f"{r['id']:<40} {r['topic'][:20]:<20} {r['status']:<12} "
            f"{(r.get('current_stage') or '')[:22]:<22} {(r.get('cluster_id') or 'NONE')[:14]:<14}"
        )
    print()


def run_layer3_with_logging(run_id: str, reset: bool = False):
    run = get_pipeline_run(run_id)
    if not run:
        print(f"❌ Run {run_id} not found.")
        sys.exit(1)

    print(f"\n📋 Run details:")
    print(f"  topic:      {run['topic']}")
    print(f"  status:     {run['status']}")
    print(f"  stage:      {run.get('current_stage')}")
    print(f"  cluster_id: {run.get('cluster_id')}")
    print(f"  gate:       {run.get('gate_status')}")
    print()

    if not run.get("cluster_id"):
        print("❌ This run has no cluster_id. Layer 2 didn't complete. Run Layer 2 first.")
        sys.exit(1)

    if reset or run["status"] == "completed":
        print("🔧 Resetting stage to layer2_done...")
        update_pipeline_run(run_id, status="running", current_stage="layer2_done")

    log_path = Path("runs") / run_id / "_live.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Tee stdout to both terminal AND _live.log so the dashboard sees it
    class Tee:
        def __init__(self, fp):
            self.fp = fp
        def write(self, s):
            sys.__stdout__.write(s)
            try:
                with open(self.fp, "a", encoding="utf-8") as f:
                    f.write(s)
                    f.flush()
            except Exception:
                pass
            return len(s)
        def flush(self):
            sys.__stdout__.flush()

    log_path.write_text("")  # truncate
    sys.stdout = Tee(str(log_path))
    sys.stderr = Tee(str(log_path))

    print(f"\n{'='*60}")
    print(f"🚀 Running Layer 3 for {run_id}")
    print(f"{'='*60}\n")

    try:
        from orchestrator import run_layer3
        run_layer3(run_id)
        print(f"\n✅ Layer 3 completed for {run_id}")
    except Exception as e:
        import traceback
        print(f"\n❌ Layer 3 crashed: {type(e).__name__}: {e}")
        print(traceback.format_exc())
        sys.exit(1)
    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Listing recent runs. To run Layer 3 for one of them:")
        print("  python run_l3.py <run_id>")
        print("  python run_l3.py <run_id> --reset")
        list_runs()
        sys.exit(0)

    run_id = sys.argv[1]
    reset = "--reset" in sys.argv
    run_layer3_with_logging(run_id, reset=reset)