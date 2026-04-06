# PAVE: Product Analysis & Verification Engine

**PAVE** is a production-grade validation suite designed to verify GOES-R satellite products by comparing data generated in the **GCCS (Ground Cloud Computing System)** against the **On-Prem (Operational)** environment.

---

## 1. Master Orchestrator: `pave.py` (v1.1.8)
The primary entry point that manages workspace initialization and sequential execution of stages 1 through 6. 

### CLI Usage
```bash
./pave.py [products] --times [YYYYDDDHH] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `products` | Positional: Product shortnames (e.g., `RadF`, `DMW`, `ABI-L2-LSA`). |
| `--times` | **Required.** 10-digit timestamps (YYYYDDDHH). |
| `--scenes` | Choices: `f`, `c`, `m1`, `m2`. |
| `--channels` | Channel list (e.g., `01`, `13`). |
| `--base-dir` | Root directory for the workspace (Default: `.`). |
| `--skip-*` | Flags to skip any specific stage (1-6). |
| `--r2-threshold` | Min R² Mean for a PASS (Default: `0.990`). |
| `-j`, `--threads` | Concurrent S3 sync threads (Default: `8`). |

---

## 2. Maintenance Utility: `archive_pave.py` (v1.1.1)
A standalone utility used to compress large data folders and reclaim disk space. It features "Workspace Intelligence" to automatically identify PAVE subdirectories.

### Key Logic
- **Archive**: Populated folders (`gccs`, `prem`, `glance`, `coll`) are compressed into `.tar.gz`.
- **Verify**: The utility verifies that the archive file count matches the source before deletion.
- **Purge**: Empty folders are removed immediately without creating an archive.
- **Protect**: The `stats/` folder is explicitly excluded from archival to keep results accessible.

### CLI Usage
```bash
./archive_pave.py [path] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `path` | Path to a PAVE workspace root (e.g., `./202418012`) or a specific subdirectory. |
| `-v`, `--verbose` | Shows the verification and purge details. |
| `-q`, `--quiet` | Suppresses all output except errors. |

---

## 3. Data Retrieval: `retrieve_pave.py` (v1.2.9)
Handles S3 discovery and mirroring. Maps GCCS cloud structures to On-Prem folder hierarchies and extracts Intermediate Products (IP) from tarballs.

### CLI Usage
```bash
./retrieve_pave.py [products] --times [YYYYDDDHH] --dest [path] [options]
```

---

## 4. Metadata Auditor: `meta_pave.py` (v1.3.7)
Performs a recursive audit of NetCDF dimensions and attributes. Fully supports `OR_I_` naming conventions for Intermediate Products.

### CLI Usage
```bash
./meta_pave.py [prem_fld] [gccs_fld] [output] [options]
```

---

## 5. Science Engine: `science_pave.py` (v1.5.5)
Wraps `glance report` to generate comparisons. Features **Cumulative Status Decoding** to report how many file pairs in a batch contained differences or missing variables.

### CLI Usage
```bash
./science_pave.py [prem_fld] [gccs_fld] [dest_fld] [options]
```

---

## 6. Collocation Engine: `collocate_pave.py` (v1.0.7)
Used for sparse data (DMW/GLM). Creates common grids for files before analysis.

### CLI Usage
```bash
./collocate_pave.py [prem_fld] [gccs_fld] [coll_fld] [dest_fld] --cfg_fld [path]
```

---

## 7. Stats Harvester: `stats_pave.py` (v2.9.4)
Scrapes Glance HTML reports to build a centralized `glance_stats_summary.csv`.

### CLI Usage
```bash
./stats_pave.py [glance_fld] [dest_fld]
```

---

## 8. The Jury: `judge_pave.py` (v1.0.1)
Renders the final PASS/FAIL verdict based on `glance_stats_summary.csv` and `metadata_audit.csv`. Robust against empty dataframes and floating-point `NaN` values.

### CLI Usage
```bash
./judge_pave.py [stats_fld] --threshold [float]
```

---

## Common Operational Flags
All modules support the standardized logging triad:
- `-v`, `--verbose`: Detailed operational logging.
- `-d`, `--debug`: Maximum verbosity (includes shell command strings).
- `-q`, `--quiet`: Only shows Warnings and Errors.
