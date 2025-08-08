# GTA DAT Toolkit — Inspector & Chase→Nodes Converter

A lightweight GUI + CLI utility for inspecting GTA `nodes.dat` / `chase.dat` files and converting `chase.dat` (20- or 28-byte variants) into `nodes.dat` ready for use in GTA: San Andreas. Fast, safe, and built for batch workflows — includes threaded conversions, automatic format detection, logs, backups, and configurable node mapping.

## Key features

* Inspect `chase.dat` and `nodes.dat` content (auto-detects 20- and 28-byte chase layouts).
* Convert `chase.dat` → `nodes.dat` with configurable scaling (default multiplier = 8.0).
* Batch convert whole folders; threaded worker pool for fast parallel conversions.
* Safe writes: optional backups and per-file conversion logs.
* CLI mode for headless automation and scriptable workflows.
* Persistent settings (multiplier, area\_id, width, type, flags, threads) saved between runs.
* Progress UI, per-file status, and detailed conversion logs that report clipping and entry counts.

## How it works

1. **Auto-detect format:** The tool reads the raw `.dat` bytes and detects whether entries are 28 bytes (classic GTA III chase variant) or 20 bytes (float+shorts variant).
2. **Parse positions:** It extracts each entry’s position (`x, y, z`) from the detected layout. For the 28-byte format, it reads the float position fields at the end of each record. For the 20-byte format, it reads the first three floats.
3. **Convert to node coordinates:** Each position is multiplied by the configured multiplier (default 8.0) and rounded to signed 16-bit integers used by `nodes.dat`.
4. **Clip & warn:** Coordinates outside `-32768..32767` are clipped. The tool logs any clipped entries for review.
5. **Write nodes.dat:** Produces a binary `nodes.dat` with a five-`uint32` header (total nodes + zeros) followed by packed node entries matching the expected `nodes` layout. A conversion log is saved next to the output file.
6. **Batching & threading:** When converting many files, the tool runs conversions in parallel using a thread pool and updates the GUI progress bar in real time.

## Usage (quick)

* GUI: `python gta dat Inspector.py` — select files or a folder, tweak settings, hit Convert.
* CLI batch:

  ```bash
  python gta dat Inspector.py --cli-batch /path/to/input_folder:/path/to/output_folder
  ```
* Make a Windows executable (optional):

  ```bash
  pyinstaller --onefile --windowed gta dat Inspector.py
  ```

## Outputs & safety

* Output files: saved as `<input>_nodes.dat` (or to chosen output folder).
* Logs: `<output>_chase_to_nodes_log.txt` with entry details and clipping counts.
* Backups: optionally create `.bak` copies of existing outputs.

## Notes

* The tool handles the structural conversion and file layout expected by SA, but in-game behavior should be tested after conversion (coordinate ranges, link offsets, or node flags may require tuning).
* Settings are saved to `~/.gta_dat_tool_config.json`.

A practical, production-ready utility for converting and inspecting GTA path data — fast, safe, and configurable for bulk workflows.
