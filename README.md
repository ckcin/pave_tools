# PAVE: Product Analysis & Verification Engine

**PAVE** is a production-grade validation suite designed to verify GOES-R satellite products by comparing data generated in the **GCCS (Ground Cloud Computing System)** against the **On-Prem (Operational)** environment using a unified comparison-driven architecture.

---

## Environment Setup
Ensure you have your AWS credentials properly configured under the `geocloud` profile. Check that your Python environment meets all requirements by running:

`python3 check_env.py`

**Core Dependencies:** `numpy`, `pandas`, `xarray`, `scipy`, `matplotlib`, `cartopy` (for geospatial plotting), `sunpy` (for SDO solar palettes), `netCDF4`, `boto3`.

---

## Pipeline Architecture (v2.0.0)
PAVE now operates on a unified 4-stage comparison-driven pipeline:

1. **STAGE 1: Data Retrieval** — S3 discovery and file mirroring
2. **STAGE 2: Metadata Audit** — NetCDF structure validation
3. **STAGE 3: Comparison Engine** — Dynamic variable routing to specialized renderers (always active)
4. **STAGE 4: Final Verdict** — Scientific quality gates and PASS/FAIL/CHECK verdicts

---

## 1. Master Orchestrator: `pave.py` (v2.0.0)
The primary entry point that manages workspace initialization and sequential execution of all pipeline stages. Comparison engine is now the default and only rendering path.

### CLI Usage
`./pave.py [products] --times [YYYYDDDHH] [options]`

### Key Arguments
| Flag | Description |
| :--- | :--- |
| `products` | Positional: Product shortnames (e.g., `RadF`, `DMW`, `ABI-L2-LSA`). |
| `--times` | **Required.** 10-digit timestamps (YYYYDDDHH). |
| `--scenes` | Scene filter (Choices: `f`, `c`, `m1`, `m2`). |
| `--channels` | Channel list filter (e.g., `01`, `13`). |
| `--sat` | Limit execution to a specific GOES satellite (`18` or `19`). |
| `--base-dir` | Root directory for the workspace (Default: `.`). |
| `--fast-compare` | **(Recommended for automation)** High-performance mode. Skips heavy standalone plots, downsamples renders for speed. |
| `--relax-match` | Relaxes file pairing constraints to match strictly on start time (`_s`) when naming conventions diverge. |
| `--skip-ip` | Safely bypasses the download of massive Intermediate Product (IP) tarballs. |
| `--skip-retrieve` | Skip STAGE 1 (data already available). |
| `--skip-meta` | Skip STAGE 2 (metadata audit). |
| `--skip-judge` | Skip STAGE 4 (final verdict). |

---

## 2. Background Automation: `pave_scheduler.py` (v2.43.0)
A continuous daemon designed to run PAVE workflows asynchronously with intelligent load balancing and automated lifecycle management.

**Key Features:**
- **+2 Hour Execution Delay:** Automatically targets data from 2 hours prior to ensure upstream generation has completed.
- **Intelligent Satellite Alternation:** Auto-routes to G19 slots (01Z, 09Z, 17Z) and G18 slots (05Z, 13Z, 21Z).
- **DOY-Cycle Load Balancing:** 3-day rotations prevent resource contention across product families.
- **Automated Lifecycle:** Native triggers for dashboard aggregation and workspace archival upon completion.
- **Fast-Compare by Default:** Scheduler typically runs with `--fast-compare` for production deployments.

### CLI Usage (Typical Production Deployment)
```bash
nohup ./pave_scheduler.py \
    --workspace /path/to/workspace \
    --dashboard /path/to/dashboard \
    --record /path/to/records \
    --fast-compare \
    --verbose \
    >> /path/to/logs/scheduler.log 2>&1 &
```

---

## 3. Comparison Engine: `compare_pave.py` (v1.13.0)
The unified rendering pipeline. It dynamically inspects NetCDF dimensions and metadata to route variables to specialized sub-engines:

### Specialized Renderers

**`compare_standard.py` (2D Spatial):**
- Projects arrays onto Geostationary maps
- Advanced **Categorical Flag Defenses** (collapses multi-dimensional bitsets, masks `_FillValue` outliers, prevents colormap smearing)

**`compare_sparse.py` (1D Tracks & Vectors):**
- Bins isolated coordinate point-clouds into 2D matrices
- Auto-pairs wind speeds/directions into **Meteorological Wind Barbs**

**`compare_profiles.py` (3D Volumetric):**
- Processes sounding matrices (LVTP/LVMP)
- Slices data at targeted pressure intervals
- Stacks into navigable 3D dashboard with geographic floor boundaries

**`compare_timeseries.py` (1D Temporal):**
- Aligns and visualizes temporal variance

---

## 4. Workspace Cleanup: `pave_archiver.py` (v3.0.0)
Standalone utility for compressing large data folders and reclaiming disk space. Features safe verification gates to ensure archives match source contents before deletion.

### CLI Usage
`./pave_archiver.py [path] [options]`

| Flag | Description |
| :--- | :--- |
| `--clean-validation` | Harvest comparison dashboard images, then archive the `validation/` directory. |
| `--dashboard` | Optional shared path to aggregate dashboard images globally. |
| `--record` | Trigger PDF generation and output artifacts to this path. |

---

## 5. Data Retrieval: `retrieve_pave.py` (v1.5.1)
Handles S3 discovery and mirroring. Maps GCCS cloud structures to On-Prem folder hierarchies and extracts Intermediate Products (IP) from tarballs.

---

## 6. Metadata Auditor: `meta_pave.py` (v1.3.7)
Performs recursive audit of NetCDF dimensions and attributes. Fully supports `OR_I_` naming conventions for Intermediate Products.

---

## 7. The Jury: `judge_pave.py` (v1.2.0)
Renders the final PASS/CHECK/FAIL verdict based on scientific statistics and metadata differences. Outlier tracing prints the exact filename causing a failure.

**Quality Gate Thresholds:**
- **PASS:** All R² ≥ 0.990
- **CHECK:** Average R² ≥ 0.990, but individual points < 0.990
- **FAIL:** Any R² < 0.900

---

## Operational Modes

### Interactive Mode (Development)
```bash
./pave.py RadF CMIP --times 2026162 --channels 08 13 --verbose
```
Default behavior: Runs all 4 stages with full output and plots.

### Automated Mode (Production)
```bash
./pave.py RadF CMIP --times 2026162 --fast-compare --quiet
```
Optimized for scheduler deployments: Comparison only, downsampled renders, minimal output.

### Scheduled Daemon (24/7 Operations)
```bash
./pave_scheduler.py --workspace /data/pave --fast-compare --record /archive/records
```
Continuous background runner with automated lifecycle management.

---

## Common Operational Flags
All modules support the standardized logging triad:
- `-v`, `--verbose`: Detailed operational logging.
- `-d`, `--debug`: Maximum verbosity (includes shell command strings and tracebacks).
- `-q`, `--quiet`: Restricts output to Warnings and Errors only.

---

## File Structure
```
pave_tools/
├── pave.py                  # Master orchestrator (v2.0.0)
├── pave_scheduler.py        # Background daemon (v2.43.0)
├── pave_archiver.py         # Workspace cleanup (v3.0.0)
├── retrieve_pave.py         # Data retrieval
├── meta_pave.py             # Metadata auditor
├── compare_pave.py          # Comparison orchestrator
├── compare_standard.py      # 2D spatial renderer
├── compare_sparse.py        # 1D track/vector renderer
├── compare_profiles.py      # 3D volumetric renderer
├── compare_timeseries.py    # 1D temporal renderer
├── compare_utils.py         # Shared plotting utilities
├── judge_pave.py            # Quality verdict engine
├── pave_utils.py            # Shared infrastructure
├── check_env.py             # Environment validation
└── glance_configs/          # GLM collocation configs
```

---

## Removed Modules (v2.0.0)
The following modules are no longer part of the active pipeline and have been removed:
- **`science_pave.py`** — Glance wrapper (superseded by comparison engine)
- **`collocate_pave.py`** — GLM collocation (superseded by compare_sparse)
- **`stats_pave.py`** — Glance stats harvester (superseded by compare_utils aggregation)

These supported legacy workflows that are now consolidated into the unified comparison architecture.

