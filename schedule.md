# PAVE Scheduler: Operations & Load Balancing Architecture

The `pave_scheduler.py` daemon is designed to continuously verify GOES-R satellite products across their entire diurnal cycle. To prevent server overload when evaluating 33 complex product pipelines, the scheduler utilizes a **3-Day Rotating Load Balancer** synchronized to the Day-of-Year (DOY).

---

## 1. Satellite & Time Slot Alternation
To ensure both GOES-18 and GOES-19 are verified across their respective daily timelines, the scheduler alternates targets throughout the day:

* **GOES-19 Data Targets:** `01:00Z`, `09:00Z`, `17:00Z`
* **GOES-18 Data Targets:** `05:00Z`, `13:00Z`, `21:00Z`

**Execution Delay (+2 Hours):**
To accommodate upstream data preparation and transfer times in the cloud, the scheduler physically executes the pipeline exactly 2 hours *after* the target data slot. (e.g., The `01:00Z` data is evaluated at `03:00Z`).

---

## 2. The 3 Product Groups
The scheduler mathematically divides the master product list into three payload blocks using the formula: `(product_index + daily_slot_index) % 3 == (DOY % 3)`.

*(Note: **ACM** (Clear Sky Mask) ignores the rotation and runs natively at the start of **every** slot to ensure continuous baseline cloud detection).*

### **Group A**
* **Imagery & Winds:** Rad/CMIP (Ch 01, 04, 07, 10, 13, 16) + DMW (Ch 07, 10)
* **Cloud Families:** COMP (COD, CPS), Cloud_EOCH
* **Surface Albedo Family:** LSA, BRF *(+ Daily NBAR/BRDF Quirks)*
* **Scalars:** RRQPE, LST, ETE
* **Cryosphere:** Cryo_AICE *(Automatically snaps to nearest 3-hour mark)*

### **Group B**
* **Imagery & Winds:** Rad/CMIP (Ch 02, 05, 08, 11, 14) + DMW (Ch 02, 08) + DMWV (Ch 08)
* **Sounding Family:** LVMP, LVTP, DSI, TPW, LSP
* **Cloud Families:** Cloud_ACT, Cloud_CCL
* **Aerosol Family:** Aerosol_ADP
* **Scalars:** FDC, ESC
* **Cryosphere:** Cryo_AITA *(Automatically snaps to nearest 3-hour mark)*

### **Group C**
* **Imagery & Winds:** Rad/CMIP (Ch 03, 06, 09, 12, 15) + DMW (Ch 09)
* **Cloud Families:** CloudHeight (ACH, CTP), Cloud_ECBH
* **Radiation Family:** RSR, DSR, PAR, SWR
* **Aerosol Family:** Aerosol_AOD
* **Scalars:** SST, FSC, ESU

---

## 3. The 3-Day Rotation Schedule

### Important Clarification: Daily Execution vs. The Diurnal Sweep
A common misconception is that a product might "skip" a day in this 3-day rotation. This is **not** the case.
* **Daily Execution:** *Every single product* runs exactly twice every day (once for GOES-18 and once for GOES-19).
* **The Diurnal Sweep:** What takes 3 days is covering the *entire 24-hour clock*. For example, on Day 1, Group A might only be evaluated during the Mid-Day slots (09z & 13z). On Day 2, it shifts to the Late Night slots (17z & 21z). On Day 3, it shifts to the Early Morning slots (01z & 05z). It takes exactly 3 days for a product to "sweep" across all 6 time slots to establish a complete 24-hour diurnal perspective.

To visualize how the groups shift downward one slot every day:

### **DAY 1 (e.g., DOY 100)**
* **[03:00Z Execution]** Target: **01Z** Data (G19) ➔ Evaluates **Group B**
* **[07:00Z Execution]** Target: **05Z** Data (G18) ➔ Evaluates **Group B**
* **[11:00Z Execution]** Target: **09Z** Data (G19) ➔ Evaluates **Group A** * **[15:00Z Execution]** Target: **13Z** Data (G18) ➔ Evaluates **Group A** * **[19:00Z Execution]** Target: **17Z** Data (G19) ➔ Evaluates **Group C** *(Triggers 12Z G19 NBAR/BRDF)*
* **[23:00Z Execution]** Target: **21Z** Data (G18) ➔ Evaluates **Group C** *(Triggers 14Z G18 NBAR/BRDF)*

### **DAY 2 (e.g., DOY 101)**
*The groups shift up. Group C is now evaluated during the early morning hours instead of the late night.*
* **[03:00Z Execution]** Target: **01Z** Data (G19) ➔ Evaluates **Group C**
* **[07:00Z Execution]** Target: **05Z** Data (G18) ➔ Evaluates **Group C**
* **[11:00Z Execution]** Target: **09Z** Data (G19) ➔ Evaluates **Group B**
* **[15:00Z Execution]** Target: **13Z** Data (G18) ➔ Evaluates **Group B**
* **[19:00Z Execution]** Target: **17Z** Data (G19) ➔ Evaluates **Group A**
* **[23:00Z Execution]** Target: **21Z** Data (G18) ➔ Evaluates **Group A**

### **DAY 3 (e.g., DOY 102)**
*The groups shift again. Group A now takes the early morning slots.*
* **[03:00Z Execution]** Target: **01Z** Data (G19) ➔ Evaluates **Group A**
* **[07:00Z Execution]** Target: **05Z** Data (G18) ➔ Evaluates **Group A**
* **[11:00Z Execution]** Target: **09Z** Data (G19) ➔ Evaluates **Group C**
* **[15:00Z Execution]** Target: **13Z** Data (G18) ➔ Evaluates **Group C**
* **[19:00Z Execution]** Target: **17Z** Data (G19) ➔ Evaluates **Group B**
* **[23:00Z Execution]** Target: **21Z** Data (G18) ➔ Evaluates **Group B**

---

## 4. Special Triggers & Quirks

* **NBAR / BRDF Offsets:** Because `NBAR` and `BRDF` require the accumulation of daytime `LSA` (Surface Albedo) data, they cannot be evaluated globally at standard synoptic hours. When the Surface Albedo group is scheduled, the engine triggers specific temporal offsets:
  * **G19** runs NBAR/BRDF at **12:00Z** (Evaluated during the 17Z slot).
  * **G18** runs NBAR/BRDF at **14:00Z** (Evaluated during the 21Z slot).
* **Cryosphere Alignments:** `AICE` and `AITA` generate every 3 hours (00, 03, 06, 09, 12, 15, 18, 21), which frequently mismatches the standard slot hours. The scheduler dynamically floors the active slot hour down to the most recently completed 3-hour increment specifically for these products.
