# KIO Hybrid AI Node

## Purpose

KIO uses a hybrid agent architecture: cloud AI is the decision and orchestration layer; the Mac mini is a persistent local execution and archive node.

```text
KOHEY
  ↓ vision / priorities / approval
ChatGPT — AI Chief of Staff / Strategy & PMO (cloud)
  ↓ plans / safe structured instructions
GitHub — command queue / source of truth / audit trail
  ↓
Mac mini — KIO Local Execution Node
  ├─ Archive / file monitoring
  ├─ Knowledge Engine refresh
  ├─ AI Assistant pack generation
  ├─ Git repository observation / safe sync
  └─ allowlisted local processing
```

## Operating principle

**Cloud decides. Local observes and executes.**

The local node is not intended to become a second autonomous Chief of Staff. It provides capabilities that the cloud cannot directly provide: access to local files and external disks, persistent monitoring, local processing, and Mac-specific execution.

The existing Archive AI remains read-only toward source documents. The node controller invokes only allowlisted local workflows and safe Git operations.

### Default hybrid boundary

Allowed to run locally without a cloud decision:

- file-change detection
- archive / Knowledge Engine refresh after local file changes
- heartbeat and health reporting
- GitHub PR / CI observation
- read-only repository status checks

Disabled by default unless explicitly enabled or requested:

- automatic GitHub Actions reruns
- autonomous Engineering Loop repair
- autonomous Codex code-repair PR creation
- policy, publication, security, or business decisions

The installer performs a one-time migration that turns autonomous CI retry and Engineering Loop repair off while preserving unrelated local settings.

## Scheduled knowledge refresh

The existing LaunchAgent can continue to run archive and knowledge refresh tasks. These are local data-maintenance operations, not business decisions.

Install or refresh it with:

```bash
./scripts/install_launch_agent.sh
```

## KIO local execution node

Install or migrate the local node:

```bash
cd "/Users/phikohey/KIO/NakadachiArchiveAI"
git pull --ff-only origin main
zsh scripts/install_kio_node_agent.sh
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

## Cloud-to-local command channel

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
- `engineering_loop` — explicitly run the bounded engineering cycle when cloud/human judgment calls for it

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
- Decide when local execution is useful
- Create safe execution instructions
- Review GitHub/local results
- Escalate only decisions that require KOHEY

### Mac mini / Local Execution Node

- Observe local files and technical state
- Execute allowlisted recurring maintenance
- Execute explicit cloud-issued jobs
- Refresh local knowledge
- Inspect and safely sync GitHub
- Return execution evidence
- Never independently change business policy or make approval decisions

## Safety

- Source documents remain read-only.
- No arbitrary remote shell execution.
- Command issues from other GitHub users are ignored.
- `github_sync` uses `git pull --ff-only` and refuses to pull over a dirty worktree.
- Autonomous CI reruns and autonomous Engineering Loop repairs are disabled by default.
- Results are recorded in GitHub and local heartbeat/log files.
