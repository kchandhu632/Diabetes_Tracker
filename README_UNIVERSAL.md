# Universal Medical PDF Diabetes Tracker

This version accepts uploaded PDF reports with different layouts and extracts whatever supported information is actually present. It no longer hard-codes the Ramesh report as the only supported format.

## What is extracted

- Patient name, ID, age, gender when identifiable
- Blood glucose readings (fasting, post-prandial, random, and direct glucose labels)
- HbA1c
- Meal/diet plan entries when meal labels are present
- Exercise/protocol entries when exercise labels and durations/frequency are present
- Medications with dose when identifiable from a medication section
- Other common laboratory results
- Diagnosis/clinical impression text when section headings are identifiable
- Recommendations/plan text
- Extraction counts and warnings
- Raw report preview

Missing information is returned as `null` or an empty array. The application does not invent a value when a PDF does not contain it.

## Universal JSON shape

```json
{
  "report": {},
  "patient": {},
  "glucose": [],
  "hba1c": {},
  "meals": [],
  "exercise": [],
  "medications": [],
  "lab_results": [],
  "diagnoses": [],
  "findings": [],
  "recommendations": [],
  "raw_sections": [],
  "extraction": {}
}
```

The frontend also stores the complete extraction in `localStorage` as `latestReportData` and provides **Download Universal JSON** and **View Universal JSON** after analysis.

## Run on Windows

1. Install Python 3.8+.
2. Open this folder.
3. Run `RUN_UNIVERSAL_PROJECT.bat`.
4. Upload any text-readable PDF report.
5. The four dashboard blocks are replaced with the new report's available data. Old entries are cleared so data from an earlier PDF cannot remain visible.

## Important limitation

A normal text PDF can be parsed using the included `pypdf` extractor. A scanned/image-only PDF may require OCR. The backend reports a warning when no readable text is extracted.

Extraction is heuristic: different laboratories use different labels and table layouts, so users should verify extracted values against the original report.
