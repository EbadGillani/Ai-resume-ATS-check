"""AI Resume ATS Checker
Streamlit UI + Google Gemini Flash.

Upload a resume (PDF / DOCX / TXT), optionally paste a job description, and get:
  * an estimated ATS score (0-100) with a category breakdown
  * strengths, missing keywords, formatting issues
  * prioritised, concrete improvements and sample bullet rewrites
"""

from __future__ import annotations

import io
import json
import os
import re
from typing import Any

import streamlit as st
from docx import Document
from pypdf import PdfReader

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_FILE_MB = 5
MAX_CHARS = 30_000          # resume text sent to the model (plenty for any resume)
MAX_JD_CHARS = 10_000       # job description text sent to the model
MIN_CHARS = 150             # below this we assume the file has no readable text

# Weights used to compute the overall score in code (so it is consistent
# with the category scores the model returns). Must sum to 1.0.
CATEGORY_WEIGHTS = {
    "keywords_and_skills": 0.30,
    "impact_and_achievements": 0.25,
    "formatting_and_parseability": 0.20,
    "structure_and_sections": 0.15,
    "language_and_readability": 0.10,
}
CATEGORY_LABELS = {
    "keywords_and_skills": "Keywords & Skills",
    "impact_and_achievements": "Impact & Achievements",
    "formatting_and_parseability": "Formatting & ATS Parseability",
    "structure_and_sections": "Structure & Sections",
    "language_and_readability": "Language & Readability",
}


# --------------------------------------------------------------------------- #
# File parsing
# --------------------------------------------------------------------------- #
class ResumeReadError(Exception):
    """Raised when a resume file cannot be turned into usable text."""


def extract_text_from_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many PDFs are "encrypted" with an empty user password.
            if not reader.decrypt(""):
                raise ResumeReadError("This PDF is password-protected. Please upload an unlocked copy.")
        pages = [(page.extract_text() or "") for page in reader.pages]
    except ResumeReadError:
        raise
    except Exception as exc:  # corrupted / unsupported PDF
        raise ResumeReadError(f"Could not read this PDF ({exc.__class__.__name__}). Is the file corrupted?") from exc
    return "\n".join(pages)


def extract_text_from_docx(data: bytes) -> str:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        raise ResumeReadError(f"Could not read this DOCX ({exc.__class__.__name__}). Is the file corrupted?") from exc

    parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    # Many resume templates put content inside tables.
    for table in doc.tables:
        for row in table.rows:
            seen: set[int] = set()
            cells = []
            for cell in row.cells:
                if id(cell._tc) in seen:  # merged cells repeat the same element
                    continue
                seen.add(id(cell._tc))
                if cell.text.strip():
                    cells.append(cell.text.strip())
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_resume_text(filename: str, data: bytes) -> str:
    """Return cleaned text from an uploaded resume file."""
    name = filename.lower()
    if name.endswith(".pdf"):
        text = extract_text_from_pdf(data)
    elif name.endswith(".docx"):
        text = extract_text_from_docx(data)
    elif name.endswith(".txt"):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise ResumeReadError("Unsupported file type. Please upload a PDF, DOCX or TXT file.")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < MIN_CHARS:
        raise ResumeReadError(
            "I could not find enough readable text in this file. If it is a scanned image or a "
            "design-heavy PDF, an ATS would struggle with it too - export a text-based PDF or DOCX and retry."
        )
    return text


# --------------------------------------------------------------------------- #
# Prompt + response handling
# --------------------------------------------------------------------------- #
def build_prompt(resume_text: str, job_description: str | None) -> str:
    jd_block = (
        f"JOB DESCRIPTION (score keyword match against this):\n\"\"\"\n{job_description[:MAX_JD_CHARS]}\n\"\"\""
        if job_description and job_description.strip()
        else "No job description was provided. Judge keywords against general industry standards "
             "for the role this resume appears to target, and say which role you inferred."
    )
    return f"""You are an expert technical recruiter and ATS (Applicant Tracking System) specialist.
Evaluate the resume below the way a strict ATS parser plus a recruiter would.

Return ONLY a JSON object (no markdown, no commentary) with exactly this shape:
{{
  "target_role": "role the resume targets or the JD's role",
  "category_scores": {{
    "keywords_and_skills": <int 0-100>,
    "impact_and_achievements": <int 0-100>,
    "formatting_and_parseability": <int 0-100>,
    "structure_and_sections": <int 0-100>,
    "language_and_readability": <int 0-100>
  }},
  "summary": "2-3 sentence overall assessment",
  "strengths": ["up to 5 short items"],
  "missing_keywords": ["up to 15 important skills/keywords that are absent"],
  "formatting_issues": ["ATS parsing or layout problems you can infer from the text"],
  "improvements": [
    {{"priority": "high|medium|low", "section": "section name", "issue": "what is wrong", "fix": "specific action to take"}}
  ],
  "bullet_rewrites": [
    {{"original": "weak bullet copied from the resume", "improved": "stronger version using action verb + metric/result"}}
  ]
}}

Scoring guidance:
- keywords_and_skills: coverage of relevant hard skills, tools, certifications, job-title terms.
- impact_and_achievements: quantified results, ownership, action verbs vs. duty lists.
- formatting_and_parseability: clear standard headings, consistent dates, no signs of tables/columns/graphics breaking text order, contact info present.
- structure_and_sections: has contact, summary, experience, education, skills; sensible order and length.
- language_and_readability: concise, no typos, consistent tense, no filler or clichés.
Be honest and calibrated: an average resume scores 55-70; only excellent ones exceed 85.
Give 5-10 improvements sorted high priority first, and 3-5 bullet rewrites using ONLY facts already in the resume
(use placeholders like [X%] where a metric is missing - never invent numbers).

{jd_block}

RESUME TEXT:
\"\"\"
{resume_text[:MAX_CHARS]}
\"\"\"
"""


def parse_json_response(raw: str) -> dict[str, Any]:
    """Parse JSON from a model reply, tolerating code fences / stray prose."""
    if not raw or not raw.strip():
        raise ValueError("Empty response from the model.")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("The model did not return valid JSON.")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Unexpected JSON structure from the model.")
    return data


def _clamp_score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    # Skip nulls explicitly: str(None) would otherwise become the text "None".
    return [str(v).strip() for v in value if v is not None and str(v).strip()][:limit]


def normalize_result(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the model output and compute the overall score deterministically."""
    raw_scores = data.get("category_scores") if isinstance(data.get("category_scores"), dict) else {}
    scores = {key: _clamp_score(raw_scores.get(key, 0)) for key in CATEGORY_WEIGHTS}
    overall = round(sum(scores[k] * w for k, w in CATEGORY_WEIGHTS.items()))

    order = {"high": 0, "medium": 1, "low": 2}
    improvements = []
    for item in data.get("improvements", []) if isinstance(data.get("improvements"), list) else []:
        if not isinstance(item, dict):
            continue
        priority = str(item.get("priority", "medium")).lower().strip()
        priority = priority if priority in order else "medium"
        improvements.append(
            {
                "priority": priority,
                "section": str(item.get("section", "General")).strip() or "General",
                "issue": str(item.get("issue", "")).strip(),
                "fix": str(item.get("fix", "")).strip(),
            }
        )
    improvements.sort(key=lambda i: order[i["priority"]])

    rewrites = []
    for item in data.get("bullet_rewrites", []) if isinstance(data.get("bullet_rewrites"), list) else []:
        if isinstance(item, dict) and str(item.get("original", "")).strip() and str(item.get("improved", "")).strip():
            rewrites.append({"original": str(item["original"]).strip(), "improved": str(item["improved"]).strip()})

    return {
        "overall": overall,
        "scores": scores,
        "target_role": str(data.get("target_role", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "strengths": _str_list(data.get("strengths"), 5),
        "missing_keywords": _str_list(data.get("missing_keywords"), 15),
        "formatting_issues": _str_list(data.get("formatting_issues"), 10),
        "improvements": improvements[:12],
        "bullet_rewrites": rewrites[:6],
    }


def analyze_resume(
    client: Any, model: str, resume_text: str, job_description: str | None, types_module: Any
) -> dict[str, Any]:
    """Call Gemini and return a normalised result. Retries once on malformed JSON."""
    prompt = build_prompt(resume_text, job_description)
    config = types_module.GenerateContentConfig(temperature=0.2, response_mime_type="application/json")

    last_error: Exception | None = None
    for _ in range(2):
        response = client.models.generate_content(model=model, contents=prompt, config=config)
        try:
            return normalize_result(parse_json_response(response.text or ""))
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
    raise ValueError(f"Could not read the AI response after 2 attempts: {last_error}")


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def get_api_key() -> str | None:
    """Look in Streamlit secrets, then env var; the sidebar input is handled in main()."""
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return str(st.secrets["GEMINI_API_KEY"])
    except Exception:  # no secrets file present
        pass
    return os.getenv("GEMINI_API_KEY")


def score_label(score: int) -> tuple[str, str]:
    if score >= 80:
        return "Excellent", "green"
    if score >= 65:
        return "Good - room to improve", "orange"
    if score >= 50:
        return "Needs work", "orange"
    return "Poor - major fixes needed", "red"


def render_results(result: dict[str, Any]) -> None:
    label, color = score_label(result["overall"])
    left, right = st.columns([1, 2])
    with left:
        st.metric("Estimated ATS Score", f"{result['overall']} / 100")
        st.markdown(f":{color}[**{label}**]")
        if result["target_role"]:
            st.caption(f"Target role: {result['target_role']}")
    with right:
        st.write(result["summary"])
    st.progress(result["overall"] / 100)

    st.subheader("Score breakdown")
    cols = st.columns(len(CATEGORY_LABELS))
    for col, (key, name) in zip(cols, CATEGORY_LABELS.items()):
        col.metric(name, result["scores"][key])
        col.progress(result["scores"][key] / 100)

    tab_fix, tab_kw, tab_rewrite, tab_good = st.tabs(
        ["Improvements", "Keywords & Formatting", "Bullet rewrites", "Strengths"]
    )

    icons = {"high": "🔴", "medium": "🟠", "low": "🟢"}
    with tab_fix:
        if not result["improvements"]:
            st.info("No improvements returned.")
        for item in result["improvements"]:
            with st.container(border=True):
                st.markdown(f"{icons[item['priority']]} **{item['priority'].title()}** · {item['section']}")
                if item["issue"]:
                    st.markdown(f"**Issue:** {item['issue']}")
                if item["fix"]:
                    st.markdown(f"**Fix:** {item['fix']}")

    with tab_kw:
        st.markdown("**Missing keywords**")
        if result["missing_keywords"]:
            st.write(" ".join(f"`{k}`" for k in result["missing_keywords"]))
        else:
            st.success("No major keywords missing.")
        st.markdown("**Formatting / parsing issues**")
        if result["formatting_issues"]:
            for issue in result["formatting_issues"]:
                st.markdown(f"- {issue}")
        else:
            st.success("No formatting issues detected.")

    with tab_rewrite:
        if not result["bullet_rewrites"]:
            st.info("No rewrites returned.")
        for item in result["bullet_rewrites"]:
            with st.container(border=True):
                st.markdown(f"**Before:** {item['original']}")
                st.markdown(f"**After:** {item['improved']}")
        st.caption("Replace placeholders like [X%] with your real numbers. Do not invent metrics.")

    with tab_good:
        if result["strengths"]:
            for s in result["strengths"]:
                st.markdown(f"- ✅ {s}")
        else:
            st.info("No strengths returned.")

    st.download_button(
        "Download report (JSON)",
        data=json.dumps(result, indent=2),
        file_name="ats_report.json",
        mime="application/json",
    )


# --------------------------------------------------------------------------- #
# Main app
# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="AI Resume ATS Checker", page_icon="📄", layout="wide")
    st.title("📄 AI Resume ATS Checker")
    st.write(
        "Upload your resume to get an estimated ATS score and specific improvements. "
        "Paste a job description for a tailored keyword match."
    )

    with st.sidebar:
        st.header("Settings")
        api_key = get_api_key()
        if not api_key:
            api_key = st.text_input(
                "Gemini API key",
                type="password",
                help="Get a free key at https://aistudio.google.com/apikey",
            )
        else:
            st.success("API key loaded from secrets / environment.")
        model = st.text_input("Gemini model", value=DEFAULT_MODEL)
        st.caption(
            "No real ATS publishes a universal score, so this is an AI-based estimate of how well "
            "your resume would parse and match. Use it as guidance, not a guarantee."
        )

    uploaded = st.file_uploader("Upload resume", type=["pdf", "docx", "txt"])
    job_description = st.text_area(
        "Job description (optional)", height=160, placeholder="Paste the job posting here for a tailored score..."
    )

    if st.button("Analyze resume", type="primary", disabled=uploaded is None):
        if not api_key:
            st.error("Please provide a Gemini API key in the sidebar.")
            return
        data = uploaded.getvalue()
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            st.error(f"File is larger than {MAX_FILE_MB} MB. Please upload a smaller file.")
            return

        try:
            with st.spinner("Reading your resume..."):
                text = extract_resume_text(uploaded.name, data)
        except ResumeReadError as exc:
            st.error(str(exc))
            return

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            with st.spinner("Analyzing with Gemini..."):
                result = analyze_resume(client, model.strip() or DEFAULT_MODEL, text, job_description, types)
        except ValueError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            msg = str(exc)
            if "API key" in msg or "API_KEY" in msg or "401" in msg or "403" in msg:
                st.error("The Gemini API key looks invalid or lacks access. Check the key and try again.")
            elif "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                st.error("Rate limit or quota reached on the Gemini API. Wait a minute and try again.")
            elif "404" in msg or "NOT_FOUND" in msg:
                st.error(f"Model '{model}' was not found. Check the model name in the sidebar.")
            else:
                st.error(f"Gemini request failed: {msg}")
            return

        st.session_state["result"] = result

    if "result" in st.session_state:
        render_results(st.session_state["result"])


if __name__ == "__main__":
    main()
