# PAVE: Product Analysis & Verification Engine

**PAVE** is a production-grade validation suite designed to verify GOES-R satellite products by comparing data generated in the **GCCS (Ground Cloud Computing System)** against the **On-Prem (Operational)** environment.

---

## Environment Setup
Ensure you have your AWS credentials properly configured under the `geocloud` profile. Check that your Python environment meets all requirements by running:

```bash
python3 check_env.py
```

**Core Dependencies:** `numpy`, `pandas`, `xarray`, `scipy`, `matplotlib`, `cartopy` (for geospatial plotting), `sunpy` (for SDO solar palettes), `netCDF4`, `boto3`.

---

## 1. Master Orchestrator: `pave.py` (v1.3.13)
The primary entry point that manages workspace initialization and sequential execution of all pipeline stages. 

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
| `--use-compare`| **(Recommended)** Bypasses legacy Glance and routes all variables through the dynamic `compare_pave.py` engine. |
| `--skip-ip` | Safely bypasses the download of massive Intermediate Product (IP) tarballs. |
| `--skip-*` | Flags to skip any specific stage (`retrieve`, `meta`, `science`, `collocate`, `stats`, `judge`). |
| `-j`, `--threads` | Concurrent S3 sync threads (Default: `8`). |

---

## 2. Maintenance Utility: `archive_pave.py` (v1.1.1)
A standalone utility used to compress large data folders and reclaim disk space. It features "Workspace Intelligence" to automatically identify PAVE subdirectories.

### CLI Usage
```bash
./archive_pave.py [path] [options]
```

---

## 3. Data Retrieval: `retrieve_pave.py` (v1.4.0)
Handles S3 discovery and mirroring. Maps GCCS cloud structures to On-Prem folder hierarchies and extracts Intermediate Products (IP) from tarballs. Now supports fast-fail `--skip-ip` bypassing.

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

## 5. Lightweight Science Engine: `compare_pave.py` (v1.6.3)
Replaces legacy tools by dynamically applying specialized comparison strategies (2D Images, 1D Time-Series, Sparse Spatial Gridding).
- **Geospatial Projections:** Uses Cartopy to natively project ABI data onto `Geostationary` maps with coastlines.
- **Solar Palettes:** Automatically maps SUVI imagery using standard NASA SDO `sunpy` colormaps.
- **Vector Flow:** Dynamically converts winds into geographical colored quiver plots.
- **Standalone Outputs:** Generates combined 2x2 dashboards as well as high-res standalone PNGs for PREM, GCCS, Differences, and Histograms.

### CLI Usage
```bash
./compare_pave.py [prem_fld] [gccs_fld] [dest_fld] [options]
```

---

## 6. Legacy Science Engine: `science_pave.py` (v1.5.5)
Wraps `glance report` to generate older comparisons. Features Cumulative Status Decoding to report how many file pairs in a batch contained differences. *(Skipped if `--use-compare` is active).*

---

## 7. Legacy Collocation Engine: `collocate_pave.py` (v1.0.7)
Used for sparse data (DMW/GLM) in the legacy pipeline. Creates common grids for files before analysis. *(Skipped if `--use-compare` is active).*

---

## 8. Stats Harvester: `stats_pave.py` (v2.9.4)
Scrapes legacy Glance HTML reports to build a centralized `glance_stats_summary.csv`.

---

## 9. The Jury: `judge_pave.py` (v1.2.0)
Renders the final PASS/CHECK/FAIL verdict based on scientific statistics and metadata differences. 
- **Outlier Tracing:** Directly prints the exact filename (e.g., `OR_ABI-L2-DMWF_G18_s...`) of the specific timestep that caused a product to be flagged as CHECK or FAIL.

### CLI Usage
```bash
./judge_pave.py [stats_fld] [options]
```

---

## 10. Environment Checker: `check_env.py`
Utility script to verify that the PAVE pipeline's environment has all required dependencies installed for geospatial and solar visualization.

### CLI Usage
```bash
./check_env.py
```

---

## Common Operational Flags
All modules support the standardized logging triad:
- `-v`, `--verbose`: Detailed operational logging.
- `-d`, `--debug`: Maximum verbosity (includes shell command strings).
- `-q`, `--quiet`: Only shows Warnings and Errors.
