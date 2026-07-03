# Nakadachi Archive AI

Nakadachi Archive AI is a read-only local knowledge-base and business-assistant system for cultural projects. It indexes files on a Mac, external HDD, and locally synced Google Drive folders, then helps AI retrieve evidence for planning, marketing, sales, grant applications, document creation, and decision support.

It never moves, deletes, renames, or edits source files.

## Structure

```text
NakadachiArchiveAI/
├── README.md
├── requirements.txt
├── config/
│   ├── config.yaml
│   └── project_profiles.json
├── scripts/
│   ├── run_archive.sh
│   ├── run_assistant.sh
│   ├── run_auto_update.sh
│   ├── install_launch_agent.sh
│   ├── run_knowledge_engine.sh
│   └── run_full_update.sh
└── src/
    ├── auto_update.py
    ├── ai_classifier.py
    ├── assistant_ai.py
    ├── classify_rules.py
    ├── exporter.py
    ├── extractors.py
    ├── knowledge_engine.py
    ├── scan_archive.py
    └── search_archive.py
```

## Run Indexing

```bash
cd "/Users/phikohey/Documents/AI・自動化研究所/NakadachiArchiveAI"
./scripts/run_archive.sh
```

Safe preflight:

```bash
./scripts/run_archive.sh --dry-run --limit 100
```

Full update for the index, Knowledge Engine, and all AI assistant packs:

```bash
./scripts/run_full_update.sh
```

## Automatic Refresh

Run one automatic refresh:

```bash
./scripts/run_auto_update.sh --once
```

Check automatic-refresh status:

```bash
python3 -B src/auto_update.py status
```

Install the macOS LaunchAgent for continuous background refresh:

```bash
./scripts/install_launch_agent.sh
```

The LaunchAgent runs at login and every 6 hours. If `/Volumes/Transcend` or a Google Drive sync folder is unavailable, it records a warning and continues with the available scan roots. New files become searchable after the next refresh.

Outputs are written to:

```text
~/NakadachiArchiveAI/output/
~/NakadachiArchiveAI/logs/
~/NakadachiArchiveAI/state/
```

Generated files include:

- `archive_index.csv`
- `archive_index.json`
- `archive_index.sqlite`
- `duplicate_report.csv`
- `duplicate_report.json`
- `summary.md`

Existing output files are not overwritten; timestamped files are created.

## Search

```bash
python3 src/search_archive.py search "KIO"
python3 src/search_archive.py context "大阪文化万博 助成金" --limit 8
python3 src/search_archive.py related "ロクソフェス" --format markdown
python3 src/search_archive.py inspect --format markdown
```

The SQLite database includes the `ai_search_documents` view for direct AI/SQL access.

## AI Knowledge Engine

```bash
./scripts/run_knowledge_engine.sh
python3 -B src/knowledge_engine.py status
```

Knowledge Engine outputs are written to timestamped folders:

```text
~/NakadachiArchiveAI/knowledge_engine/run_YYYYMMDD_HHMMSS/
```

Each run contains:

- `knowledge_manifest.json`
- `AI_KNOWLEDGE_ENGINE.md`
- `knowledge_graph.json`
- `knowledge_engine.sqlite`
- `project_maps/*.md`
- `task_briefs/*.md`

AI should start from `knowledge_manifest.json`, open the relevant project map, then use the task brief for planning, marketing, sales, grant applications, presentation creation, or decision support.

## AI Business Assistant

```bash
./scripts/run_assistant.sh list-projects
./scripts/run_assistant.sh ask "KIOの助成金申請に使える実績資料を探して申請骨子を作る"
./scripts/run_assistant.sh brief osaka_fringe --task grant --limit 12
./scripts/run_assistant.sh ask "ロクソドンタブラックの営業用プレゼン構成を作る" --task presentation --limit 12
./scripts/run_assistant.sh build-packs --task all
```

Assistant outputs are written to:

```text
~/NakadachiArchiveAI/assistant_output/
```

Current project profiles:

- KIO
- ロクソドンタブラック
- Osaka Fringe / 大阪文化万博
- Osaka Culture Quest
- なにわ大賞
- TACT/FEST
- 阿倍野区民センター

Assistant packets include retrieved evidence, related materials, task frames, and practical draft outputs for:

- 企画立案
- マーケティング
- 営業・提案
- 助成金申請
- 資料作成
- プレゼン資料作成
- 意思決定

The assistant uses the latest generated SQLite index automatically. It searches indexed file metadata, extracted text, OCR text, and AI classification fields, then creates evidence-backed drafts without changing source files.

## Safety

- Source files are read-only inputs.
- Existing folder structures are never changed.
- Symbolic links are not followed.
- Missing optional scan roots, such as an unplugged HDD, are warnings rather than file-processing errors.
- Permission errors are logged and scanning continues.
- Automatic refresh only creates indexes, knowledge maps, logs, and state files under `~/NakadachiArchiveAI/`.

## Optional System Tools

The system works with Python standard library only. Optional tools improve extraction:

- `pdftotext`: PDF text extraction
- `tesseract`: OCR
- `ffprobe`: video/audio metadata
- `mdls`: macOS metadata fallback
