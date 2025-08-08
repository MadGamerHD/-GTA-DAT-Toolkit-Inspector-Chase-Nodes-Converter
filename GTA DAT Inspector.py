#!/usr/bin/env python3
"""
GTA DAT Inspector + Chase->Nodes Converter (improved & optimized)

Features:
 - Inspect nodes.dat & chase.dat (20/28 byte variants)
 - Convert single chase.dat -> nodes.dat
 - Batch convert all .dat files in a folder
 - Threaded conversions with progress bar
 - Backup originals, logs, config persistence
 - CLI mode for headless batch conversion
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext
import struct
import os
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import shutil
import threading
import queue
import sys

# ------------ Defaults & helpers ------------
HOME = Path.home()
CONFIG_PATH = HOME / ".gta_dat_tool_config.json"
DEFAULT_CONFIG = {
    "multiplier": 8.0,
    "area_id": 0,
    "width": 0,
    "node_type": 0,
    "flags": 0,
    "backup": True,
    "threads": 4,
    "max_preview": 200
}

FMT_28 = "<hhh10bfff"   # GTA III 28-byte layout used earlier
FMT_20 = "<fffhhhh"     # 20-byte variant (x,y,z + 4 shorts)
NODES_ENTRY_FMT = "<II3hHHHHBBI"
HEADER_FMT = "<IIIII"

# Thread-safe UI queue
ui_q = queue.Queue()

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                conf = json.load(f)
                DEFAULT_CONFIG.update(conf)
        except Exception:
            pass

def save_config(conf):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(conf, f, indent=2)
    except Exception:
        pass

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# ------------ Core parsing & conversion logic ------------
def detect_chase_variant(data: bytes):
    if len(data) % 28 == 0:
        return 28
    if len(data) % 20 == 0:
        return 20
    return None

def parse_chase_positions(data: bytes):
    """Return list of (x,y,z) floats parsed from either 28- or 20-byte variant."""
    variant = detect_chase_variant(data)
    if variant == 28:
        entries = []
        count = len(data) // 28
        for i in range(count):
            chunk = data[i*28:(i+1)*28]
            unpacked = struct.unpack(FMT_28, chunk)
            # in fmt we used earlier, pos floats are last 3 fields
            px, py, pz = unpacked[13], unpacked[14], unpacked[15]
            entries.append((px, py, pz))
        return entries, 28
    elif variant == 20:
        entries = []
        count = len(data) // 20
        for i in range(count):
            chunk = data[i*20:(i+1)*20]
            x, y, z, *_ = struct.unpack(FMT_20, chunk)
            entries.append((x, y, z))
        return entries, 20
    else:
        return None, None

def convert_positions_to_nodes(entries, multiplier, defaults):
    """
    entries: list of (px,py,pz) floats
    multiplier: how to convert float->node integer (hw used 8.0)
    defaults: dict with area_id, width, node_type, flags
    returns bytes for nodes.dat and log lines
    """
    node_bytes = bytearray()
    log_lines = []
    clipped = 0
    for idx, (px, py, pz) in enumerate(entries):
        xi = int(round(px * multiplier))
        yi = int(round(py * multiplier))
        zi = int(round(pz * multiplier))

        # clip to signed short
        if not (-32768 <= xi <= 32767) or not (-32768 <= yi <= 32767) or not (-32768 <= zi <= 32767):
            clipped += 1
            xi = max(-32768, min(32767, xi))
            yi = max(-32768, min(32767, yi))
            zi = max(-32768, min(32767, zi))

        mem_addr = 0
        unused = 0
        marker = 0
        link_offset = 0
        area_id = int(defaults.get("area_id", 0)) & 0xFFFF
        node_id = idx & 0xFFFF
        width = int(defaults.get("width", 0)) & 0xFFFF
        node_type = int(defaults.get("node_type", 0)) & 0xFF
        flags = int(defaults.get("flags", 0)) & 0xFF

        packed = struct.pack(NODES_ENTRY_FMT, mem_addr, unused, xi, yi, zi, marker, link_offset, area_id, node_id, width, node_type, flags)
        node_bytes.extend(packed)
        log_lines.append(f"{idx}: pos=({px:.3f},{py:.3f},{pz:.3f}) -> nodePos=({xi},{yi},{zi}) id={node_id}")

    header = struct.pack(HEADER_FMT, len(entries), 0, 0, 0, 0)
    return header + node_bytes, log_lines, clipped

# ------------ File conversion wrapper used by threads ------------
def convert_file_worker(chase_path: Path, out_path: Path, config: dict, defaults: dict, backup: bool):
    """
    Convert one file. Returns (success, msg, details)
    details: dict with clipped_count, entries_count, log_path
    """
    start = now()
    try:
        data = chase_path.read_bytes()
        parse_result, variant = parse_chase_positions(data)
        if parse_result is None:
            return False, f"Unknown variant for {chase_path.name}", {"entries": 0}
        entries = parse_result
        binary, lines, clipped = convert_positions_to_nodes(entries, config["multiplier"], defaults)

        # Ensure output folder exists
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Backup if requested and output exists (not backing up the source)
        if backup and out_path.exists():
            bak = out_path.with_suffix(out_path.suffix + ".bak")
            shutil.copy2(out_path, bak)

        # Write nodes file
        out_path.write_bytes(binary)

        # Write log next to output
        log_path = out_path.with_name(out_path.stem + "_chase_to_nodes_log.txt")
        with open(log_path, "w") as L:
            L.write(f"Converted: {chase_path}\n")
            L.write(f"Variant: {variant}-byte entries\n")
            L.write(f"Time (UTC): {start}\n")
            L.write(f"Entries: {len(entries)}\n")
            L.write(f"Clipped: {clipped}\n\n")
            L.write("\n".join(lines))

        return True, f"Converted {chase_path.name} ({len(entries)} entries, clipped={clipped})", {"entries": len(entries), "clipped": clipped, "log": str(log_path)}
    except Exception as e:
        return False, f"Error converting {chase_path.name}: {e}", {"entries": 0}

# ------------ GUI application ------------
class GTAConverterApp:
    def __init__(self, root):
        load_config()
        self.config = DEFAULT_CONFIG.copy()
        self.defaults = {"area_id": self.config["area_id"], "width": self.config["width"], "node_type": self.config["node_type"], "flags": self.config["flags"]}
        self.root = root
        root.title("GTA DAT Toolkit — Inspector & Chase→Nodes (Optimized)")
        root.geometry("1100x640")

        # Top controls
        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=6)

        self.lbl_file = ttk.Label(top, text="No selection")
        self.lbl_file.pack(side="left", padx=6)

        ttk.Button(top, text="Select File(s)...", command=self.select_files).pack(side="left", padx=4)
        ttk.Button(top, text="Select Folder...", command=self.select_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Inspect Selected", command=self.inspect_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Convert Selected → nodes", command=self.convert_selected).pack(side="left", padx=8)
        ttk.Button(top, text="Batch Convert Folder", command=self.batch_convert_folder).pack(side="left", padx=4)

        # Progress and settings area
        middle = ttk.Frame(root)
        middle.pack(fill="x", padx=8, pady=6)

        self.progress = ttk.Progressbar(middle, length=400, mode="determinate")
        self.progress.pack(side="left", padx=6)

        settings_frame = ttk.LabelFrame(middle, text="Conversion Settings")
        settings_frame.pack(side="right", padx=6)

        ttk.Label(settings_frame, text="multiplier").grid(row=0, column=0, sticky="e")
        self.mult_var = tk.DoubleVar(value=self.config["multiplier"])
        ttk.Entry(settings_frame, textvariable=self.mult_var, width=6).grid(row=0, column=1, padx=4)

        ttk.Label(settings_frame, text="area_id").grid(row=0, column=2, sticky="e")
        self.area_var = tk.IntVar(value=self.config["area_id"])
        ttk.Entry(settings_frame, textvariable=self.area_var, width=5).grid(row=0, column=3, padx=4)

        ttk.Label(settings_frame, text="width").grid(row=1, column=0, sticky="e")
        self.width_var = tk.IntVar(value=self.config["width"])
        ttk.Entry(settings_frame, textvariable=self.width_var, width=5).grid(row=1, column=1, padx=4)

        ttk.Label(settings_frame, text="type").grid(row=1, column=2, sticky="e")
        self.type_var = tk.IntVar(value=self.config["node_type"])
        ttk.Entry(settings_frame, textvariable=self.type_var, width=5).grid(row=1, column=3, padx=4)

        ttk.Label(settings_frame, text="flags").grid(row=2, column=0, sticky="e")
        self.flags_var = tk.IntVar(value=self.config["flags"])
        ttk.Entry(settings_frame, textvariable=self.flags_var, width=5).grid(row=2, column=1, padx=4)

        self.backup_var = tk.BooleanVar(value=self.config.get("backup", True))
        ttk.Checkbutton(settings_frame, text="Backup existing outputs", variable=self.backup_var).grid(row=2, column=2, columnspan=2)

        ttk.Label(settings_frame, text="threads").grid(row=3, column=0, sticky="e")
        self.threads_var = tk.IntVar(value=self.config.get("threads", 4))
        ttk.Entry(settings_frame, textvariable=self.threads_var, width=4).grid(row=3, column=1, padx=4)

        # Middle - treeview listing files
        self.tree = ttk.Treeview(root, columns=("path", "variant", "entries", "status"), show="headings", height=12)
        for c in ("path", "variant", "entries", "status"):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=260 if c=="path" else 100, anchor="center")
        self.tree.pack(fill="both", padx=8, pady=6)

        # Bottom - log window
        bottom = ttk.Frame(root)
        bottom.pack(fill="both", expand=True, padx=8, pady=6)
        self.log = scrolledtext.ScrolledText(bottom, height=10, state="disabled")
        self.log.pack(fill="both", expand=True)

        # statusbar
        self.statusbar = ttk.Label(root, text="Ready")
        self.statusbar.pack(fill="x")

        # internal
        self.selected_files = []
        self.executor = None
        self.stop_event = threading.Event()

        # start UI update poller for thread results
        self.root.after(200, self._process_ui_queue)

    # ---------- UI actions ----------
    def select_files(self):
        paths = filedialog.askopenfilenames(title="Select .dat file(s)", filetypes=[("DAT files", "*.dat")])
        if not paths:
            return
        self.selected_files = [Path(p) for p in paths]
        self._refresh_tree()
        self.lbl_file.config(text=f"{len(self.selected_files)} file(s) selected")

    def select_folder(self):
        folder = filedialog.askdirectory(title="Select folder with .dat files")
        if not folder:
            return
        p = Path(folder)
        self.selected_files = sorted([f for f in p.glob("*.dat")])
        self._refresh_tree()
        self.lbl_file.config(text=f"Folder: {folder} ({len(self.selected_files)} .dat files)")

    def inspect_selected(self):
        self._clear_tree_status()
        for path in self.selected_files:
            try:
                data = path.read_bytes()
                variant = detect_chase_variant(data)
                entries, _ = parse_chase_positions(data) if variant else (None, None)
                if entries is not None:
                    self._update_tree_row(path, variant, len(entries), "OK")
                    self._log(f"{path.name}: variant={variant}, entries={len(entries)}")
                else:
                    # maybe it's nodes
                    # try to read header to see if nodes header present
                    with open(path, "rb") as f:
                        header = f.read(20)
                        if len(header) >= 20:
                            try:
                                total_nodes = struct.unpack("<I", header[:4])[0]
                                self._update_tree_row(path, "nodes?", total_nodes, "NodeFile?")
                                self._log(f"{path.name}: looks like nodes.dat (header total_nodes={total_nodes})")
                            except Exception:
                                self._update_tree_row(path, "unknown", 0, "Unknown")
                                self._log(f"{path.name}: unknown format")
                        else:
                            self._update_tree_row(path, "tiny", 0, "Too small")
            except Exception as e:
                self._update_tree_row(path, "err", 0, f"Err: {e}")
                self._log(f"Failed to inspect {path}: {e}")

    def convert_selected(self):
        if not self.selected_files:
            messagebox.showinfo("No files", "Select one or more chase.dat files first.")
            return
        # default output location: same folder with suffix _nodes.dat
        out_files = []
        for p in self.selected_files:
            out = p.with_name(p.stem + "_nodes.dat")
            out_files.append((p, out))
        # run conversions in a thread pool
        self._run_batch(out_files)

    def batch_convert_folder(self):
        if not self.selected_files:
            messagebox.showinfo("No files", "Select a folder first (use 'Select Folder...').")
            return
        # confirm output folder selection
        outdir = filedialog.askdirectory(title="Select output folder (converted nodes saved here)")
        if not outdir:
            return
        outdir = Path(outdir)
        out_files = []
        for p in self.selected_files:
            out = outdir / (p.stem + "_nodes.dat")
            out_files.append((p, out))
        self._run_batch(out_files)

    # ---------- conversion orchestration ----------
    def _run_batch(self, file_pairs):
        # save config from UI
        self.config["multiplier"] = float(self.mult_var.get())
        self.config["area_id"] = int(self.area_var.get())
        self.config["width"] = int(self.width_var.get())
        self.config["node_type"] = int(self.type_var.get())
        self.config["flags"] = int(self.flags_var.get())
        self.config["backup"] = bool(self.backup_var.get())
        self.config["threads"] = int(self.threads_var.get())
        save_config(self.config)

        # prepare UI
        total = len(file_pairs)
        self.progress["maximum"] = total
        self.progress["value"] = 0
        self.statusbar.config(text=f"Converting {total} files...")

        # thread pool
        max_workers = max(1, min(16, self.config["threads"]))
        self._log(f"Starting conversion: {total} files, threads={max_workers}")
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = []
        for src, dst in file_pairs:
            fut = self.executor.submit(convert_file_worker, src, dst, self.config, self.defaults, self.config["backup"])
            futures.append((fut, src, dst))

        # gather results asynchronously and push to UI queue
        def waiter():
            completed = 0
            for fut, src, dst in futures:
                try:
                    success, msg, details = fut.result()
                except Exception as e:
                    success, msg, details = False, f"Exception: {e}", {}
                completed += 1
                ui_q.put(("progress", completed, total))
                ui_q.put(("result", str(src), success, msg, details, str(dst)))
            ui_q.put(("done", total))
        threading.Thread(target=waiter, daemon=True).start()

    # ---------- UI helpers & updates ----------
    def _refresh_tree(self):
        # clear
        for r in self.tree.get_children():
            self.tree.delete(r)
        for p in self.selected_files:
            self.tree.insert("", "end", values=(str(p), "-", "-", "Queued"))

    def _clear_tree_status(self):
        for r in self.tree.get_children():
            vals = list(self.tree.item(r, "values"))
            vals[2] = "-"
            vals[3] = "-"
            self.tree.item(r, values=vals)

    def _update_tree_row(self, path: Path, variant, entries, status):
        # find tree item matching path
        for r in self.tree.get_children():
            vals = list(self.tree.item(r, "values"))
            if vals[0] == str(path):
                vals[1] = variant
                vals[2] = entries
                vals[3] = status
                self.tree.item(r, values=vals)
                return
        # if not found, add it
        self.tree.insert("", "end", values=(str(path), variant, entries, status))

    def _log(self, text):
        timestamped = f"[{now()}] {text}\n"
        self.log.configure(state="normal")
        self.log.insert("end", timestamped)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _process_ui_queue(self):
        # handle messages from worker thread
        try:
            while True:
                item = ui_q.get_nowait()
                if not item:
                    continue
                if item[0] == "progress":
                    completed, total = item[1], item[2]
                    self.progress["value"] = completed
                    self.statusbar.config(text=f"Progress: {completed}/{total}")
                elif item[0] == "result":
                    src, success, msg, details, dst = item[1], item[2], item[3], item[4], item[5]
                    self._log(msg)
                    # update tree row for source file
                    try:
                        p = Path(src)
                        self._update_tree_row(p, "-", details.get("entries", "-"), "OK" if success else "Failed")
                    except Exception:
                        pass
                elif item[0] == "done":
                    self._log(f"Batch complete: {item[1]} files")
                    self.statusbar.config(text="Ready")
                    self.progress["value"] = 0
        except queue.Empty:
            pass
        # schedule again
        self.root.after(200, self._process_ui_queue)

# ------------ CLI support ------------
def run_cli_batch(input_folder: Path, output_folder: Path, cfg: dict, defaults: dict, backup: bool):
    files = sorted(input_folder.glob("*.dat"))
    if not files:
        print("No .dat files found in", input_folder)
        return
    pairs = [(f, output_folder / (f.stem + "_nodes.dat")) for f in files]
    max_workers = max(1, min(16, cfg.get("threads", 4)))
    print(f"Starting batch conversion: {len(pairs)} files, threads={max_workers}")
    with ThreadPoolExecutor(max_workers=max_workers) as exec:
        futures = {exec.submit(convert_file_worker, src, dst, cfg, defaults, backup): (src, dst) for src, dst in pairs}
        for fut in as_completed(futures):
            src, dst = futures[fut]
            ok, msg, details = fut.result()
            print(msg)
    print("Batch done.")

# ------------ Main entrypoint ------------
def main():
    parser = argparse.ArgumentParser(description="GTA DAT Toolkit — Inspector & Chase→Nodes")
    parser.add_argument("--cli-batch", help="Batch convert all .dat in INPUT folder to OUTPUT folder (usage: --cli-batch input:output)", default=None)
    args = parser.parse_args()

    # load config
    load_config()
    cfg = DEFAULT_CONFIG.copy()
    defaults = {"area_id": cfg["area_id"], "width": cfg["width"], "node_type": cfg["node_type"], "flags": cfg["flags"]}

    if args.cli_batch:
        try:
            inp, outp = args.cli_batch.split(":")
            inp = Path(inp).expanduser()
            outp = Path(outp).expanduser()
            outp.mkdir(parents=True, exist_ok=True)
            run_cli_batch(inp, outp, cfg, defaults, cfg.get("backup", True))
            return
        except Exception as e:
            print("CLI batch argument malformed or failed:", e)
            return

    # GUI mode
    root = tk.Tk()
    app = GTAConverterApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()