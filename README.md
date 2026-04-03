# PAVE: Product Analysis & Verification Engine

**PAVE** is a production-grade validation suite designed to verify GOES-R satellite products by comparing data generated in the **GCCS (Ground Cloud Computing System)** against the **On-Prem (Operational)** environment.

---

## 1. Master Orchestrator: `pave.py` (v1.1.5)
The primary entry point that manages workspace initialization and sequential execution of all sub-modules. It creates a standardized directory structure and passes the necessary context to each stage.

### CLI Usage
```bash
./pave.py [products] --times [YYYYDDDHH] [options]
```

### Arguments
| Flag | Type | Description |
| :--- | :--- | :--- |
| `products` | Positional | One or more product shortnames (e.g., `RadF`, `ABI-L2-LSA`). |
| `--times` | String(s) | **Required.** 10-digit timestamps (YYYYDDDHH). |
| `--scenes` | Choices | Filter by `f`, `c`, `m1`, or `m2`. |
| `--channels` | String(s) | Filter by channel (e.g., `01`, `13`). |
| `--prefix` | String | Custom prefix for the job folder. |
| `--tag` | String | Custom suffix/tag for the job folder. |
| `--base-dir` | Path | Root directory for workspace (Default: `.`). |
| `--skip-retrieve` | Switch | Skip Stage 1: Data Mirroring. |
| `--skip-meta` | Switch | Skip Stage 2: Metadata Audit. |
| `--skip-science` | Switch | Skip Stage 3: Science Reports. |
| `--skip-collocate` | Switch | Skip Stage 4: Sparse Data Alignment. |
| `--skip-stats` | Switch | Skip Stage 5: Statistics Harvesting. |
| `--skip-judge` | Switch | Skip Stage 6: PASS/FAIL Verdict. |
| `--r2-threshold` | Float | Minimum R² Mean for a PASS (Default: `0.990`). |
| `-j`, `--threads` | Int | Concurrent S3 sync threads (Default: `8`). |
| `--bin` | Path | Path to the `glance` executable (Default: `glance`). |

---

## 2. Data Retrieval: `retrieve_pave.py` (v1.2.9)
Handles complex S3 discovery and mirroring. It is responsible for mapping GCCS's cloud structure to On-Prem's GPAS folder hierarchy and extracting IP data from tarballs.

### CLI Usage
```bash
./retrieve_pave.py [products] --times [YYYYDDDHH] --dest [path] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `products` | List of products to retrieve. |
| `--times` | Target timestamps for retrieval. |
| `--scenes` | Scene filtering (Full Disk, CONUS, Mesoscale). |
| `--channels` | Specific ABI channels to include. |
| `--dest` | Root folder where `gccs/` and `prem/` subdirs will be created. |
| `-j`, `--threads` | Max threads for parallel `aws s3 sync` calls. |

---

## 3. Metadata Auditor: `meta_pave.py` (v1.3.7)
Performs a recursive audit of NetCDF dimensions and attributes. It matches files by their full identity (OR_..._sYYYY...) and handles the `OR_I_` naming convention for Intermediate Products.

### CLI Usage
```bash
./meta_pave.py [prem_fld] [gccs_fld] [output] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `prem_fld` | Positional: Path to the mirrored On-Prem data. |
| `gccs_fld` | Positional: Path to the mirrored GCCS data. |
| `output` | Positional: Filename for the CSV report or destination folder. |

---

## 4. Science Engine: `science_pave.py` (v1.5.4)
Wraps the `glance report` utility. It generates HTML-based visual and statistical comparisons. It is designed to ignore "Exit 4" (differences found) and "Exit 80" (no variables) to ensure pipeline continuity.

### CLI Usage
```bash
./science_pave.py [prem_fld] [gccs_fld] [dest_fld] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `prem_fld` | Path to On-Prem data source. |
| `gccs_fld` | Path to GCCS data source. |
| `dest_fld` | Folder where Glance HTML reports will be stored. |
| `--fork` | Enable parallel report generation within Glance. |
| `--bin` | Specific path to the `glance` binary. |

---

## 5. Collocation Engine: `collocate_pave.py` (v1.0.7)
Used for sparse/point data (DMW/GLM). It creates common spatial and temporal grids for files before they are sent to the Science Engine.

### CLI Usage
```bash
./collocate_pave.py [prem_fld] [gccs_fld] [coll_fld] [dest_fld] --cfg_fld [path] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `prem_fld` | Mirrored On-Prem source. |
| `gccs_fld` | Mirrored GCCS source. |
| `coll_fld` | Workspace for temporary collocated NetCDF files. |
| `dest_fld` | Destination for the resulting collocated science reports. |
| `--cfg_fld` | **Required.** Directory containing `.py` collocate configuration files. |

---

## 6. Stats Harvester: `stats_pave.py` (v2.9.4)
Scrapes the Glance-generated HTML reports to build a centralized time-series CSV of metrics (primarily R² correlation and data fractions).

### CLI Usage
```bash
./stats_pave.py [glance_fld] [dest_fld] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `glance_fld` | Path to the root directory of Glance HTML reports. |
| `dest_fld` | Path to output the consolidated `glance_stats_summary.csv`. |

---

## 7. The Jury: `judge_pave.py` (v1.0.1)
Renders the final PASS/FAIL verdict by comparing statistics and metadata audits against production quality gates.

### CLI Usage
```bash
./judge_pave.py [stats_fld] [options]
```

### Arguments
| Flag | Description |
| :--- | :--- |
| `stats_fld` | Folder containing `glance_stats_summary.csv` and `metadata_audit.csv`. |
| `--threshold` | The minimum R² Mean score required to pass (Default: `0.990`). |

---

## Common Operational Flags
Every module in the PAVE suite supports the standardized logging triad:
- `-v`, `--verbose`: Enables detailed operational logging.
- `-d`, `--debug`: Maximum verbosity (includes shell command strings).
- `-q`, `--quiet`: Suppresses non-essential logs; only shows Warnings and Errors.
