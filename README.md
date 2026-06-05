# PAVE: Product Analysis & Verification Engine

**PAVE** is a production-grade validation suite designed to verify GOES-R satellite products by comparing data generated in the **GCCS (Ground Cloud Computing System)** against the **On-Prem (Operational)** environment.

---

## Environment Setup
Ensure you have your AWS credentials properly configured under the `geocloud` profile. Check that your Python environment meets all requirements by running:

`python3 check_env.py`

**Core Dependencies:** `numpy`, `pandas`, `xarray`, `scipy`, `matplotlib`, `cartopy` (for geospatial plotting), `sunpy` (for SDO solar palettes), `netCDF4`, `boto3`.

---

## 1. Master Orchestrator: `pave.py` (v1.6.1)
The primary entry point that manages workspace initialization and sequential execution of all pipeline stages.

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
| `--use-compare`| Bypasses legacy Glance and routes variables through the dynamic comparison engines. |
| `--fast-compare`| **(Recommended)** High-performance mode. Skips heavy standalone plots, visualizes via downsampled striding, and implicitly enables `--use-compare`. |
| `--relax-match` | Relaxes file pairing constraints to match strictly on start time (`_s`) when naming conventions diverge. |
| `--skip-ip` | Safely bypasses the download of massive Intermediate Product (IP) tarballs. |

---

## 2. Background Automation: `pave_scheduler.py` (v2.36.2)
A continuous daemon designed to run PAVE workflows asynchronously. It tracks DOY (Day-of-Year) cycles to balance loads across 3-day rotations.
- **+2 Hour Execution Delay:** Automatically sleeps and targets data from 2 hours prior to ensure upstream generation has completed.
- **Intelligent Satellite Alternation:** Auto-routes slots (01Z, 09Z, 17Z for G19; 05Z, 13Z, 21Z for G18).
- **Automated Lifecycle:** Native triggers for dashboard aggregation and workspace archival upon completion.

### CLI Usage
`./pave_scheduler.py --workspace /path/to/work --fast-compare`

---

## 3. High-Performance Compare Engine: `compare_pave.py` (v1.12.0)
The heart of the modern pipeline. It dynamically inspects NetCDF dimensions and metadata to route variables to one of four specialized rendering sub-engines:

* **`compare_standard.py` (2D Spatial):** Projects arrays onto Geostationary maps. Features advanced **Categorical Flag Defenses** (collapses multi-dimensional bitsets, explicitly masks `_FillValue` outliers, and prevents discrete colormap smearing via `nearest` interpolation).
* **`compare_sparse.py` (1D Tracks & Vectors):** Bins isolated coordinate point-clouds into 2D matrices. Automatically pairs wind speeds and directions into mapped **Meteorological Wind Barbs**.
* **`compare_profiles.py` (3D Volumetric):** Intercepts sounding matrices (LVTP/LVMP). Intelligently rotates axes, slices data at targeted pressure intervals, and stacks them into a navigable 3D dashboard anchored by Cartopy geographic floor boundaries.
* **`compare_timeseries.py` (1D Temporal):** Aligns and visualizes temporal variance.

---

## 4. Workspace Cleanup Utility: `pave_archiver.py` (v1.6.0)
A standalone utility used to compress large data folders and reclaim disk space. Features safe "Verification Gates" to ensure archives perfectly match source contents before deletion.

### CLI Usage
`./pave_archiver.py [path] [options]`

| Flag | Description |
| :--- | :--- |
| `--clean-validation` | Includes the visually dense `validation/` output directory in the archival sweep. |
| `--clean-glance` | Validates and purges legacy HTML glance report folders. |

---

## 5. Data Retrieval: `retrieve_pave.py` (v1.4.0)
Handles S3 discovery and mirroring. Maps GCCS cloud structures to On-Prem folder hierarchies and extracts Intermediate Products (IP) from tarballs.

---

## 6. Metadata Auditor: `meta_pave.py` (v1.3.7)
Performs a recursive audit of NetCDF dimensions and attributes. Fully supports `OR_I_` naming conventions for Intermediate Products.

---

## 7. The Jury: `judge_pave.py` (v1.2.0)
Renders the final PASS/CHECK/FAIL verdict based on scientific statistics and metadata differences. Outlier tracing prints the exact filename causing a failure.

---

## 8. Legacy Engines (Glance / Collocation / Stats Harvester)
For backward compatibility, the suite still supports `science_pave.py`, `collocate_pave.py`, and `stats_pave.py`. These wrap the legacy `glance report` utility and are skipped automatically when `--use-compare` or `--fast-compare` is invoked.

---

## Common Operational Flags
All modules support the standardized logging triad:
- `-v`, `--verbose`: Detailed operational logging.
- `-d`, `--debug`: Maximum verbosity (includes shell command strings and tracebacks).
- `-q`, `--quiet`: Restricts output to Warnings and Errors only.
