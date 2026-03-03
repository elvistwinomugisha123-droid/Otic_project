#!/bin/bash
# ============================================================
# Otic IT Support Agent — Project Bootstrap Script
# Run: chmod +x bootstrap.sh && ./bootstrap.sh
# ============================================================

set -e

PROJECT_DIR="otic-it-agent"
echo "🚀 Scaffolding $PROJECT_DIR..."

mkdir -p "$PROJECT_DIR"/{agent,tools,rag,memory,observability,data/runbooks,diagrams,tests}

# Create all __init__.py files
for dir in agent tools rag memory observability; do
    touch "$PROJECT_DIR/$dir/__init__.py"
done

# Create placeholder Python files
touch "$PROJECT_DIR/app.py"
touch "$PROJECT_DIR/agent/core.py"
touch "$PROJECT_DIR/agent/prompts.py"
touch "$PROJECT_DIR/agent/safety.py"
touch "$PROJECT_DIR/tools/registry.py"
touch "$PROJECT_DIR/tools/kb_search.py"
touch "$PROJECT_DIR/tools/log_search.py"
touch "$PROJECT_DIR/tools/server_metrics.py"
touch "$PROJECT_DIR/tools/status_check.py"
touch "$PROJECT_DIR/tools/create_ticket.py"
touch "$PROJECT_DIR/rag/indexer.py"
touch "$PROJECT_DIR/rag/retriever.py"
touch "$PROJECT_DIR/memory/store.py"
touch "$PROJECT_DIR/observability/tracer.py"
touch "$PROJECT_DIR/tests/test_agent_loop.py"
touch "$PROJECT_DIR/tests/test_safety.py"
touch "$PROJECT_DIR/tests/test_tools.py"

# Create data placeholders
touch "$PROJECT_DIR/data/dummy_logs.json"
touch "$PROJECT_DIR/data/dummy_metrics.json"
touch "$PROJECT_DIR/data/dummy_services.json"

# Create runbook placeholders
for runbook in vpn_troubleshooting disk_usage_cleanup website_performance service_restart_diagnosis email_sync_issues general_triage; do
    touch "$PROJECT_DIR/data/runbooks/${runbook}.md"
done

echo "✅ Project structure created!"
echo ""
echo "Next steps:"
echo "  1. cd $PROJECT_DIR"
echo "  2. cp ../.env.example .env  (then add your API key)"
echo "  3. cp ../CLAUDE.md ."
echo "  4. cp ../requirements.txt ."
echo "  5. cp ../diagrams/architecture.mermaid diagrams/"
echo "  6. python -m venv venv && source venv/bin/activate"
echo "  7. pip install -r requirements.txt"
echo "  8. claude  (start Claude Code CLI)"
echo ""
echo "🎯 Give Claude Code the instruction:"
echo '   "Read CLAUDE.md, then implement the agent starting with agent/core.py"'