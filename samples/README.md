# Demo samples (fictional)

Every document here is invented and rendered with a visible **نموذج / SPECIMEN — FICTIONAL**
watermark, fictional authority names and no real emblems or security features. Regenerate them
with `python -m jobs.samples --out samples`.

| Session | What it shows | Expected result |
|---|---|---|
| `clean/` | All four documents agree | `auto_pass` once real readers agree (with default, uncalibrated weights it may still route) |
| `missing_grandfather/` | The license owner name omits the grandfather name | `human_review`, reason `NAME_PARTIAL_MATCH`; the cross-check detail names the missing token |
| `glare_on_id/` | Glare over the National Card number | `id_number` **blank and flagged** (`unreadable`), retake request "Glare over the ID number" |

`ground_truth.json` in each folder holds the printed values.

```bash
python -m khatti.admin seed-demo                                   # prints a demo API key
python -m khatti.demo samples/glare_on_id --api-key <key>          # create -> upload -> submit -> result
```
