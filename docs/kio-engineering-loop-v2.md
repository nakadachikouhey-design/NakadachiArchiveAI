# KIO Engineering Loop v2

KIO Engineering Loop v2 extends the v1 detect/diagnose/classify loop with constrained Codex repair proposals for deterministic YELLOW CI failures.

## Flow

`Detect -> Diagnose -> Classify -> Repair workspace -> Codex edit -> Guardrail inspection -> Local validation -> Commit/Push -> PR -> CI re-verification`

## Classification

- GREEN: transient infrastructure failures. One bounded failed-job rerun only.
- YELLOW / environment or permission: escalation only.
- YELLOW / deterministic code or test: eligible for a constrained repair PR.
- RED: Public API safety, RLS, anonymous access, service-role, unpublished-data or publication-boundary concerns. Automatic repair is prohibited.

## v2 automatic repair guardrails

Codex runs non-interactively with `codex exec --sandbox workspace-write --ephemeral` inside a dedicated managed Git workspace. The local node, not Codex, owns Git commit, push and PR creation.

Automatic repair is rejected if a proposed diff touches protected surfaces including GitHub workflows, Supabase/migrations, environment files, dependency manifests/lockfiles, authentication/security/public-API/RLS paths, or the local agent's own control files.

The node also rejects suspicious patches that attempt to skip tests, ignore errors, weaken gates, or modify Public API / RLS / service-role behavior.

A repair PR is opened only after repository-specific local validation passes. The loop never auto-merges a repair PR.

## Operational limits

- At most one Codex code repair is attempted per cycle by default.
- Managed repair workspaces live below `~/NakadachiArchiveAI/repair_workspaces`.
- Dirty managed workspaces are not reset automatically; the run is escalated instead.
- Missing/unavailable Codex CLI causes escalation, never a bypass.
- Existing RED invariants always take precedence over repair automation.
