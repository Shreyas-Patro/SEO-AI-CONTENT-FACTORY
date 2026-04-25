"""
Run once after dropping in the new files:
    python migrate.py

Creates the new pipeline_runs and artifacts tables, plus the artifacts/ folder.
Also adds the artifacts_dir to your config if missing.
"""

import os
import yaml

CONFIG_PATH = "config.yaml"


def ensure_config_has_artifacts_dir():
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️  {CONFIG_PATH} not found — skipping config update")
        return
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    if "paths" not in cfg:
        cfg["paths"] = {}
    if "artifacts_dir" not in cfg["paths"]:
        cfg["paths"]["artifacts_dir"] = "data/artifacts"
        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
        print("  ✓ Added paths.artifacts_dir to config.yaml")
    else:
        print("  · paths.artifacts_dir already present")


def main():
    print("Canvas Homes — migration to v2 architecture")
    print("=" * 50)

    print("\n1. Updating config.yaml...")
    ensure_config_has_artifacts_dir()

    print("\n2. Creating new SQL tables...")
    from db.sqlite_ops import init_db
    init_db()

    from db.artifacts import init_artifact_tables
    init_artifact_tables()
    print("  ✓ pipeline_runs table created")
    print("  ✓ artifacts table created")

    print("\n3. Creating artifacts directory...")
    os.makedirs("data/artifacts", exist_ok=True)
    print("  ✓ data/artifacts/ ready")

    print("\n4. Sanity check: importing all agents...")
    try:
        from agents.base import AgentBase
        print("  ✓ agents.base")
    except Exception as e:
        print(f"  ✗ agents.base: {e}")
    try:
        from agents.competitor_spy import CompetitorSpyAgent
        print("  ✓ agents.competitor_spy (v2)")
    except Exception as e:
        print(f"  ✗ agents.competitor_spy: {e}")
    try:
        from agents.keyword_mapper import KeywordMapperAgent
        print("  ✓ agents.keyword_mapper (v2)")
    except Exception as e:
        print(f"  ✗ agents.keyword_mapper: {e}")
    try:
        from agents.research_prompt_generator import ResearchPromptGeneratorAgent
        print("  ✓ agents.research_prompt_generator (NEW)")
    except Exception as e:
        print(f"  ✗ agents.research_prompt_generator: {e}")
    try:
        from pipeline import start_pipeline_run
        print("  ✓ pipeline orchestrator")
    except Exception as e:
        print(f"  ✗ pipeline: {e}")

    print("\n" + "=" * 50)
    print("✅ Migration complete.")
    print("\nNext steps:")
    print("  1. Run: streamlit run dashboard.py")
    print("  2. Enter a topic, click 'Start New Run'")
    print("  3. Click 'Run Layer 1' to test the pipeline")


if __name__ == "__main__":
    main()