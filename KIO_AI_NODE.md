# KIO Always-On AI Node

## Purpose

The Mac mini is the persistent execution node for KIO's AI operating model.

```text
KOHEY
  ↓ vision / priorities / approval
ChatGPT — AI Chief of Staff / Strategy & PMO
  ↓ structured instructions
GitHub — command queue / source of truth / audit trail
  ↓
Mac mini — KIO Always-On AI Node
  ├─ Archive / file monitoring
  ├─ Knowledge Engine refresh
  ├─ AI Assistant pack generation
  ├─ Git repository status / safe sync
  └─ scheduled local processing
```

The existing Archive AI remains read-only toward source documents. The node controller only invokes allowlisted local workflows and safe Git operations.

## Existing scheduled knowledge refresh

The existing LaunchAgent continues to run the full archive update every 6 hours:

- archive indexing
- Knowledge Engine generation
- AI assistant pack generation

Install or refresh it with:

```bash
./scripts/install_launch_agent.sh
```

## KIO local node controller

Install the local node controller:

```bash
cd "/Users/phikohey/Documents/AI・自動化研究所/NakadachiArchiveAI"
git pull --ff-only
chmod +x scripts/install_kio_node_agent.sh
./scripts/install_kio_node_agent.sh
```

It runs at login and every 10 minutes.

Status:

```bash
python3 -B src/kio_node_agent.py status
```

Local heartbeat:

```text
~/NakadachiArchiveAI/agent_state/heartbeat.json
```

Logs:

```text
~/NakadachiArchiveAI/logs/kio_local_node.out.log
~/NakadachiArchiveAI/logs/kio_local_node.err.log
```

## Chief of Staff command channel

The node watches open issues in:

```text
nakadachikouhey-design/NakadachiArchiveAI
```

A command issue must satisfy both conditions:

1. The title contains `[KIO-AGENT]`.
2. The issue author is exactly `nakadachikouhey-design`.

The issue body must be a JSON object.

Example:

```json
{"action":"full_update"}
```

Supported actions:

- `full_update` — index + Knowledge Engine + assistant packs
- `knowledge_update` — rebuild Knowledge Engine
- `archive_update` — refresh archive index
- `assistant_build` — rebuild all assistant packs
- `repo_status` — inspect current local repository state
- `github_sync` — fetch and fast-forward pull only when the worktree is clean

Arbitrary shell commands are rejected.

After execution, the Mac mini posts the result back to the GitHub issue and closes successful or rejected tasks. Failed tasks stay open for inspection.

## Role boundaries

### KOHEY

- Vision
- Priority
- Important decisions
- Important negotiations
- Creative direction

### ChatGPT / AI Chief of Staff

- Interpret intent
- Plan and prioritize work
- Create safe execution instructions
- Review GitHub results
- Escalate only decisions that require KOHEY

### Mac mini / Local Agent

- Execute allowlisted recurring work
- Refresh local knowledge
- Inspect and safely sync GitHub
- Return execution evidence
- Never independently change business policy or make approval decisions

## Safety

- Source documents remain read-only.
- No arbitrary remote shell execution.
- Command issues from other GitHub users are ignored.
- `github_sync` uses `git pull --ff-only` and refuses to pull over a dirty worktree.
- Results are recorded in GitHub and local heartbeat/log files.
