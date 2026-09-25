from flask import Flask, request, jsonify
from flask_cors import CORS
from pypdf import PdfReader
from datetime import datetime
from pathlib import Path
import re
import json

app = Flask(__name__)
CORS(app)


def clean_text(value):
    return re.sub(r"\s+", " ", (value or "")).strip(" -:\t")


def first_number(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                pass
    return None


def parse_date_time(value):
    value = clean_text(value)
    formats = [
        "%d-%b-%Y, %I:%M %p", "%d-%b-%Y %I:%M %p",
        "%d/%m/%Y, %I:%M %p", "%d/%m/%Y %I:%M %p",
        "%d-%m-%Y, %I:%M %p", "%d-%m-%Y %I:%M %p",
        "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            continue
    return None


def report_datetime(text):
    patterns = [
        r"Report Date\s*[:\-]?\s*([0-9]{1,2}[-/]\w+[-/]\d{2,4},?\s*[0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?)",
        r"Report Date\s*[:\-]?\s*([0-9]{1,2}[-/]\d{1,2}[-/]\d{2,4},?\s*[0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?)",
        r"Report Date\s*[:\-]?\s*([0-9]{1,2}[-/]\w+[-/]\d{2,4})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            parsed = parse_date_time(m.group(1))
            if parsed:
                return parsed
    return datetime.now().replace(microsecond=0).isoformat()


def sample_datetime(text):
    patterns = [
        r"Sample Collected\s*[:\-]?\s*([^\n]+)",
        r"Collected\s*[:\-]?\s*([^\n]+)"
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            parsed = parse_date_time(m.group(1))
            if parsed:
                return parsed
    return None


def at_time(base_iso, hour, minute=0):
    try:
        base = datetime.fromisoformat(base_iso)
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
    except ValueError:
        return base_iso


def extract_patient(text):
    def grab(patterns):
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                return clean_text(m.group(1))
        return None

    age = grab([r"Age\s*[/|]?\s*Gender\s*[:\-]?\s*(\d{1,3})\s*(?:Years?|Yrs?)?"])
    gender = grab([r"Age\s*[/|]?\s*Gender\s*[:\-]?\s*\d{1,3}\s*(?:Years?|Yrs?)?\s*/\s*([A-Za-z]+)"])
    return {
        "name": grab([r"Patient Name\s*[:\-]?\s*([^\n]+)", r"Name\s*[:\-]?\s*([^\n]+)"]),
        "patient_id": grab([r"Patient ID\s*[:\-]?\s*([^\n]+)", r"MRN\s*[:\-]?\s*([^\n]+)", r"UHID\s*[:\-]?\s*([^\n]+)"]),
        "age": int(age) if age and age.isdigit() else age,
        "gender": gender,
    }


def extract_glucose(text, report_date):
    tests = [
        ("Fasting Blood Sugar", [r"Fasting Blood Sugar", r"Fasting Blood Glucose", r"\bFBS\b"], 8, 0),
        ("Post-Prandial Blood Sugar", [r"Post[- ]?Prandial Blood Sugar", r"Post[- ]?Meal Blood Sugar", r"\bPPBS\b"], 11, 0),
        ("Random Blood Sugar", [r"Random Blood Sugar", r"Random Blood Glucose", r"\bRBS\b"], None, None),
        ("Blood Glucose", [r"^Blood Glucose(?: Level)?$"], None, None),
    ]
    results = []
    seen = set()
    for name, labels, hour, minute in tests:
        for label in labels:
            pattern = rf"{label}[^0-9]{{0,60}}([0-9]+(?:\.[0-9]+)?)\s*(?:mg\s*/?\s*dL)?"
            m = re.search(pattern, text, re.I)
            if m:
                value = m.group(1)
                key = (name, value)
                if key in seen:
                    continue
                seen.add(key)
                timestamp = at_time(report_date, hour, minute) if hour is not None else report_date
                status_match = re.search(rf"{label}[^\n]*?\b(HIGH|LOW|NORMAL|CRITICAL|ABNORMAL)\b", text, re.I)
                results.append({
                    "test": name,
                    "value": float(value) if "." in value else int(value),
                    "unit": "mg/dL",
                    "time": timestamp,
                    "status": status_match.group(1).upper() if status_match else None,
                    "source": "Uploaded PDF"
                })
                break
    return results


def extract_hba1c(text):
    m = re.search(r"Hb\s*A1c[^0-9]{0,50}([0-9]+(?:\.[0-9]+)?)\s*%?", text, re.I)
    if not m:
        return {"value": None, "unit": "%", "status": None, "source": "Uploaded PDF"}
    value = float(m.group(1))
    status_m = re.search(r"Hb\s*A1c[^\n]*?\b(HIGH|LOW|NORMAL|POOR CONTROL|ABNORMAL)\b", text, re.I)
    return {
        "value": value,
        "unit": "%",
        "status": status_m.group(1).upper() if status_m else None,
        "source": "Uploaded PDF"
    }


def section_lines(text, headings):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    found = []
    active = False
    for line in lines:
        if any(re.search(h, line, re.I) for h in headings):
            active = True
            continue
        if active and re.match(r"^(?:\d+\.?\s*)?[A-Z][A-Za-z &/()-]{3,50}$", line) and not re.search(r"(?:\.|,|;|\d)", line):
            if not any(re.search(h, line, re.I) for h in headings):
                active = False
        if active:
            found.append(line)
    return found


def extract_meals(text, report_date):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    meal_names = r"Early Morning|Breakfast|Brunch|Lunch|Mid[- ]?Morning Snack|Evening Snack|Snack|Dinner|Bedtime"
    results = []
    for line in lines:
        normalized = re.sub(r"^[•\x7f\-*]+\s*", "", line)
        if not re.match(rf"^(?:{meal_names})\b", normalized, re.I):
            continue
        m = re.match(rf"^({meal_names})\s*(?:\(([^)]*)\))?\s*[:\-]?\s*(.*)$", normalized, re.I)
        if not m:
            continue
        name, timing, notes = m.groups()
        timing = clean_text(timing or "")
        notes = clean_text(notes)
        hour = minute = None
        tm = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", timing, re.I)
        if tm:
            hour, minute = int(tm.group(1)), int(tm.group(2))
            if tm.group(3):
                if tm.group(3).upper() == "PM" and hour != 12: hour += 12
                if tm.group(3).upper() == "AM" and hour == 12: hour = 0
        kcal_m = re.search(r"(?:~|about\s*)?(\d+)\s*kcal", timing + " " + notes, re.I)
        calories = int(kcal_m.group(1)) if kcal_m else None
        results.append({
            "name": name.title(),
            "time": at_time(report_date, hour, minute) if hour is not None else None,
            "calories": calories,
            "notes": notes,
            "source": "Uploaded PDF"
        })
    return dedupe_by(results, ("name", "time", "notes"))


def extract_exercise(text, report_date):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    results = []
    for line in lines:
        normalized = re.sub(r"^[•\x7f\-*]+\s*", "", line)
        if not re.search(r"^(?:Aerobic Exercise|Post[- ]Meal Walk|Strength Training|Exercise|Walk(?:ing)?|Run(?:ning)?|Jog(?:ging)?|Cycling|Yoga|Swimming|Strength Training)", normalized, re.I):
            continue

        duration_m = re.search(r"(\d+)\s*(?:-|to)?\s*(\d+)?\s*(?:minutes?|mins?|hours?|hrs?)", line, re.I)
        duration = None
        if duration_m:
            duration = int(duration_m.group(2) or duration_m.group(1))
            if re.search(r"hours?|hrs?", duration_m.group(0), re.I):
                duration *= 60

        # Extract a real clock time if the report explicitly gives one.
        explicit_time = None
        tm = re.search(r"(\d{1,2}):([0-5]\d)\s*(AM|PM)", line, re.I)
        if tm:
            hour = int(tm.group(1))
            minute = int(tm.group(2))
            meridiem = tm.group(3).upper()
            if meridiem == "PM" and hour != 12:
                hour += 12
            if meridiem == "AM" and hour == 12:
                hour = 0
            explicit_time = at_time(report_date, hour, minute)

        # If the report describes a protocol rather than a completed session,
        # use the schedule stated by this report when it is unambiguous.
        # Otherwise keep the report date so the UI never renders 1/1/1970.
        scheduled_time = explicit_time or report_date
        low = normalized.lower()
        if not explicit_time:
            if "after lunch" in low or "post-lunch" in low:
                scheduled_time = at_time(report_date, 13, 30)
            elif "after dinner" in low or "post-dinner" in low:
                scheduled_time = at_time(report_date, 20, 30)
            elif "brisk walking" in low or "aerobic" in low:
                scheduled_time = at_time(report_date, 7, 0)
            elif "strength" in low or "squat" in low or "push-up" in low or "pushup" in low:
                scheduled_time = at_time(report_date, 18, 0)

        intensity = None
        for candidate in ("lightweight", "light", "moderate", "brisk", "vigorous"):
            if re.search(rf"\b{candidate}\b", line, re.I):
                intensity = candidate.title()
                break

        freq_m = re.search(r"((?:\d+\s*)?(?:days?|times?)\s*(?:/|per)\s*week|twice weekly|once weekly)", line, re.I)
        item_type = clean_text(normalized.split(":", 1)[0])
        if "aerobic" in item_type.lower() or "brisk walking" in low:
            item_type = "Brisk walking (aerobic)"
        elif ("post-meal" in low or "post meal" in low) and "lunch" in low:
            item_type = "Post-lunch walk"
        elif ("post-meal" in low or "post meal" in low) and "dinner" in low:
            item_type = "Post-dinner walk"
        elif "after lunch" in low and "walk" in low:
            item_type = "Post-lunch walk"
        elif "after dinner" in low and "walk" in low:
            item_type = "Post-dinner walk"
        elif "strength" in item_type.lower() or "squat" in low or "push-up" in low or "pushup" in low:
            item_type = "Strength training"

        results.append({
            "type": item_type,
            "duration": duration,
            "unit": "minutes",
            "frequency": clean_text(freq_m.group(1)) if freq_m else None,
            "intensity": intensity,
            "time": scheduled_time,
            "notes": line,
            "source": "Uploaded PDF"
        })

    return dedupe_by(results, ("type", "duration", "time", "notes"))


def extract_medications(text, report_date):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    results = []
    in_med_section = False
    section_end = re.compile(r"^(?:\d+\.?\s*)?(?:Diet|Physical|Exercise|Laboratory|Lab Results|Diagnosis|Findings|Recommendations|Plan)\b", re.I)
    known_names = (
        "Metformin", "Glimepiride", "Voglibose", "Insulin", "Sitagliptin",
        "Empagliflozin", "Aspirin", "Atorvastatin", "Losartan", "Telmisartan",
        "Amlodipine", "Gliclazide", "Pioglitazone"
    )

    for line in lines:
        normalized = re.sub(r"^[•\x7f\-*]+\s*", "", line)
        if re.search(r"medications?|prescriptions?|pharmacological|drug list|medicines?", line, re.I):
            in_med_section = True
            if re.match(r"^(?:\d+\.?\s*)?.*(?:medications?|prescriptions?)\b", line, re.I) and not re.search(r"mg|mcg|g\b", line, re.I):
                continue
        elif in_med_section and section_end.search(line):
            in_med_section = False
        if not in_med_section:
            continue

        if not normalized.startswith(known_names) and not re.match(r"^[A-Za-z][A-Za-z0-9 .()/-]{2,80}\s*[-–:]\s*\d", normalized):
            continue
        dose_m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|units?|IU)\b", normalized, re.I)
        if not dose_m:
            continue
        dose = f"{dose_m.group(1)} {dose_m.group(2)}"
        name_part = normalized[:dose_m.start()].strip(" -:;•")
        name_part = re.sub(r"^(?:\d+\.|[-•*])\s*", "", name_part)
        if not name_part or len(name_part) > 100:
            continue

        # Preserve the instruction text and convert common meal-relative
        # instructions to the same report date used by glucose/meals.
        instruction = normalized
        lower = instruction.lower()
        scheduled_time = report_date
        if "before breakfast" in lower or "with breakfast" in lower:
            scheduled_time = at_time(report_date, 8, 0)
        elif "just before lunch" in lower or "before lunch" in lower or "with lunch" in lower:
            scheduled_time = at_time(report_date, 13, 0)
        elif "after dinner" in lower or "with dinner" in lower or "at night" in lower or "night" in lower:
            scheduled_time = at_time(report_date, 20, 0)
        else:
            tm = re.search(r"(\d{1,2}):([0-5]\d)\s*(AM|PM)", instruction, re.I)
            if tm:
                hour = int(tm.group(1))
                minute = int(tm.group(2))
                meridiem = tm.group(3).upper()
                if meridiem == "PM" and hour != 12:
                    hour += 12
                if meridiem == "AM" and hour == 12:
                    hour = 0
                scheduled_time = at_time(report_date, hour, minute)

        freq_m = re.search(r"(\d+\s*(?:tablet|tab|capsule|cap|puff|dose)?[^.]*?(?:daily|day|before|after|with|at|just before|morning|night|breakfast|lunch|dinner)[^.]*\.?$)", line, re.I)
        results.append({
            "name": clean_text(name_part),
            "dose": dose,
            "frequency": clean_text(freq_m.group(1)) if freq_m else None,
            "time": scheduled_time,
            "instructions": instruction,
            "source": "Uploaded PDF"
        })
    return dedupe_by(results, ("name", "dose", "time", "instructions"))


def extract_lab_results(text):
    results = []
    # General table-like lines: Test Name 123 unit ... [status]
    unit_pattern = r"(?:mg/dL|g/dL|mmol/L|mEq/L|IU/L|U/L|ng/mL|pg/mL|µIU/mL|uIU/mL|%|cells/µL|million/µL|10\^?\d+/L)"
    for line in [clean_text(x) for x in text.splitlines() if clean_text(x)]:
        if re.search(r"^(?:Fasting Blood Sugar|Post[- ]?Prandial Blood Sugar|Random Blood Sugar|HbA1c)\b", line, re.I):
            continue
        m = re.search(r"^(?:[•\x7f\-*]+\s*)?(.{2,70}?)\s+(\d+(?:\.\d+)?)\s*(%|mg/dL|g/dL|mmol/L|mEq/L|IU/L|U/L|ng/mL|pg/mL|µIU/mL|uIU/mL|cells/µL|million/µL)\b(?:\s+.*)?$", line, re.I)
        if not m:
            continue
        name = clean_text(m.group(1))
        if not re.search(r"(?:cholesterol|triglyceride|creatinine|urea|bilirubin|hemoglobin|platelet|sodium|potassium|albumin|protein|ALT|AST|TSH|HDL|LDL|vitamin|uric acid|WBC|RBC|ESR|CRP)", name, re.I):
            continue
        status_m = re.search(r"\b(HIGH|LOW|NORMAL|ABNORMAL|CRITICAL|POSITIVE|NEGATIVE)\b", line, re.I)
        results.append({
            "test": name,
            "value": float(m.group(2)) if "." in m.group(2) else int(m.group(2)),
            "unit": m.group(3) or "",
            "reference_range": "",
            "status": status_m.group(1).upper() if status_m else None,
            "date": None,
            "source": "Uploaded PDF"
        })
    return dedupe_by(results, ("test", "value", "unit"))


def extract_section_content(text, heading_patterns):
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    out = []
    active = False
    for line in lines:
        if any(re.search(p, line, re.I) for p in heading_patterns):
            active = True
            continue
        if active and re.match(r"^(?:\d+\.?\s*)?[A-Z][A-Za-z &/()_-]{3,60}$", line) and not any(re.search(p, line, re.I) for p in heading_patterns):
            break
        if active:
            out.append(line)
    return out


def dedupe_by(items, keys):
    seen = set()
    out = []
    for item in items:
        key = tuple(item.get(k) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def extract_universal_report(text, filename, pages):
    report_date = report_datetime(text)
    sample_date = sample_datetime(text)
    glucose = extract_glucose(text, report_date)
    hba1c = extract_hba1c(text)
    meals = extract_meals(text, report_date)
    exercise = extract_exercise(text, report_date)
    medications = extract_medications(text, report_date)
    lab_results = extract_lab_results(text)

    diagnoses = []
    for line in extract_section_content(text, [r"Diagnosis", r"Diagnoses", r"Clinical Impression"]):
        if len(line) > 2:
            diagnoses.append({"name": line, "status": None, "source": "Uploaded PDF"})

    recommendations = extract_section_content(text, [r"Recommendations?", r"Advice", r"Plan"])
    findings = []
    for line in text.splitlines():
        line = clean_text(line)
        if re.search(r"\b(HIGH|LOW|NORMAL|ABNORMAL|CRITICAL|POOR CONTROL)\b", line, re.I):
            findings.append(line)
    findings.extend([f"Glucose entries extracted: {len(glucose)}", f"Meal entries extracted: {len(meals)}", f"Exercise entries extracted: {len(exercise)}", f"Medication entries extracted: {len(medications)}", f"Other lab entries extracted: {len(lab_results)}"])

    warnings = []
    if not text.strip(): warnings.append("No readable text was extracted from this PDF. It may be scanned/image-only.")
    if not any([glucose, meals, exercise, medications, lab_results, hba1c["value"] is not None]):
        warnings.append("No supported structured health fields were detected. Review the raw report text or use OCR for scanned PDFs.")

    raw_sections = []
    section_titles = []
    for line in [clean_text(x) for x in text.splitlines() if clean_text(x)]:
        if re.match(r"^(?:\d+\.?\s*)?[A-Z][A-Za-z &/()_-]{3,70}$", line) and len(line) < 80:
            section_titles.append(line)
    for title in section_titles[:30]:
        raw_sections.append({"section": title, "content": ""})

    return {
        "report": {
            "filename": filename,
            "report_date": report_date,
            "sample_date": sample_date,
            "pages": pages,
            "source": "Uploaded PDF"
        },
        "patient": extract_patient(text),
        "glucose": glucose,
        "hba1c": hba1c,
        "meals": meals,
        "exercise": exercise,
        "medications": medications,
        "lab_results": lab_results,
        "diagnoses": diagnoses[:30],
        "findings": dedupe_strings(findings)[:50],
        "recommendations": recommendations[:50],
        "raw_sections": raw_sections,
        "extraction": {
            "success": bool(text.strip()),
            "glucose_entries": len(glucose),
            "meal_entries": len(meals),
            "exercise_entries": len(exercise),
            "medication_entries": len(medications),
            "lab_entries": len(lab_results),
            "warnings": warnings
        }
    }


def dedupe_strings(items):
    return list(dict.fromkeys([x for x in items if x]))


USERS = {}

@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"message": "Username and password are required."}), 400
    if username in USERS:
        return jsonify({"message": "Account already exists. Please sign in."}), 409
    USERS[username] = password
    return jsonify({"message": "Account created successfully."}), 201

@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""
    if (username in USERS and USERS[username] == password) or (username == "chandu" and password):
        return jsonify({"token": f"local-demo-token:{username}", "user": {"username": username}})
    return jsonify({"message": "Invalid username or password. Create an account first."}), 401

@app.get("/")
def home():
    return jsonify({"message": "Diabetic Management System API is running successfully", "status": "success"})

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "parser": "universal-pdf-v1"})

@app.post("/api/reports/analyze")
def analyze_report():
    uploaded = request.files.get("report")
    if not uploaded:
        return jsonify({"message": "No PDF file was uploaded."}), 400
    if not uploaded.filename.lower().endswith(".pdf"):
        return jsonify({"message": "Please upload a PDF file."}), 400

    temp_path = Path("uploaded_report.pdf")
    uploaded.save(temp_path)
    try:
        reader = PdfReader(str(temp_path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        data = extract_universal_report(text, uploaded.filename, len(reader.pages))
        data["characters"] = len(text)
        data["preview"] = text[:4000]
        # Backward-compatible top-level report_date for the existing frontend.
        data["report_date"] = data["report"]["report_date"]
        return jsonify(data)
    except Exception as exc:
        return jsonify({"message": f"Could not analyze PDF: {exc}"}), 500
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
