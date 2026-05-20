# JD PARSING PROMPT (JD_Prompt.md)

You are a STRICT, evidence-based information extractor. Your task is to convert a Job Description (JD) into a clean JSON object used for resume–JD matching.
NO guesswork. NO hallucination. Evidence-based only.
If a value is not explicitly present, return null (for scalars) or [] (for arrays).

INPUT FILE:
JD_FILE_NAME: {{JD_FILE_NAME}}

JD_TEXT (UTF-8 plain text; may have headers/footers, tables, or OCR noise):
"""
{{JD_TEXT}}
"""

-----------------------------------------
OUTPUT RULES
-----------------------------------------
- Return ONE JSON object. No prose, no markdown.
- All keys MUST exist exactly as defined.
- null for missing scalar fields.
- [] for missing arrays.
- No renaming of keys.
- No assumptions — evidence only.
- Trim whitespace; deduplicate arrays.
- Skills: lowercase (e.g., "python", "azure devops").
- Role/Domain/Location: Title Case (e.g., "Data Engineer", "BFSI", "Pune").

-----------------------------------------
JSON SCHEMA (USE EXACT KEYS)
-----------------------------------------

{
  "Role_name": null,
  "Grade": null,
  "Account_name": null,
  "Business_Domain": [],
  "Work_location": [],
  "Experience_band": {
    "min_years": null,
    "max_years": null,
    "preferred_years": null
  },
  "Mandatory_tech_skills": [],
  "Nice_to_have_tech_skills": [],
  "Soft_skills": [],
  "Responsibilities": [],
  "Education": [],
  "Certifications": [],
  "Other_requirements": [],
  "Meta": {
    "jd_source_filename": null,
    "seniority_hints": [],
    "locations_raw": [],
    "skills_raw": []
  }
}

-----------------------------------------
EXTRACTION RULES
-----------------------------------------

### Role_name
- Prefer job title at top of JD.
- Else use fields labeled "Role", "Position", "Job Title", or first bold title-like string.
- Convert to Title Case.
- Evidence only.

### Grade
- Extract if JD explicitly contains ANY of the following grade/level codes ANYWHERE in the text:
  - Capgemini bands: A1 A2 A3 A4 A5 / B1 B2 / C1 C2 / D1 D2 / E1 E2 / F1 F2
  - Level codes: L1 L2 L3 L4 L5 L6 L7 L8 (e.g., "for Barcaleys (L2)" → "L2", "Senior Engineer L4" → "L4")
  - Band text: "Band 6", "Grade 5", etc.
  - Seniority labels ONLY if explicitly used as a grade/level label: "Senior", "Lead", "Architect", "Manager", "Principal"
- These codes may appear ANYWHERE: job title, role summary, body text, parentheses, or inline (e.g., "hired for Acme (L2)" → "L2").
- Scan the ENTIRE JD text — not just labeled fields.
- If not found → null.

### Account_name
- Read the ENTIRE JD and identify any organization, company, bank, client, or account that the role is being hired FOR or is WORKING WITH.
- Do NOT rely on labels or fixed patterns. Use contextual understanding — the account name can appear ANYWHERE: job title, role summary, responsibilities, project description, header, footer, or any sentence.
- Common patterns to look for:
  - "hired for <Company>" → Account_name = Company
  - "working with <Company>" → Account_name = Company
  - "for <Company> (<Grade>)" → Account_name = Company
  - "<Company> account" → Account_name = Company
  - "client: <Company>" → Account_name = Company
- It could be any type of entity: a bank, a retailer, a telecom company, a government body, an airline, a tech firm, etc.
- If multiple organizations are mentioned, pick the one that is clearly the CLIENT or END CUSTOMER (not Capgemini or the hiring/staffing company itself).
- Capture the name as-is in Title Case.
- If no client/account organization is identifiable → null.

### Business_Domain
- Extract domain/sector keywords such as:
  BFSI, Retail, Telecom, Healthcare, Insurance, Energy, Manufacturing, Logistics, Automotive, Media, Public Sector
- Split on commas, slashes, and bullets.
- Normalize to Title Case. Deduplicate.
- If not found → [].

### Work_location
- Extract explicit city/region/country names listed as acceptable locations.
- Normalize to Title Case. Deduplicate.
- Use [] if remote-only and no city is listed.

### Experience_band
- Parse patterns like: "3–6 years", "5+ years", "min 4 years", "at least 8 years", "up to 10 years".
- Populate:
  - min_years: lower bound (number or null)
  - max_years: upper bound (number or null); if only "5+ years" → max_years = null
  - preferred_years: if a preferred/ideal value is explicitly stated

### Mandatory_tech_skills
- Extract from sections labeled: Must-have / Required / Core / Mandatory / Primary.
- Lowercase tokens, split by commas/bullets.
- Strip version numbers; keep canonical names (e.g., "python", "pandas", "azure devops").

### Nice_to_have_tech_skills
- Extract from sections labeled: Good to have / Secondary / Preferred / Plus / Nice to have.
- Same normalization as above.

### Soft_skills
- Extract: communication, teamwork, stakeholder management, leadership, agile, problem solving, etc.

### Responsibilities
- Return atomic, action-focused bullet statements (e.g., "Design ETL pipelines in PySpark").
- No summaries. No merging of multiple responsibilities into one.

### Education
- Extract only explicit education requirements (e.g., "B.E./B.Tech in Computer Science").

### Certifications
- Extract explicit certifications (e.g., "AWS Certified Solutions Architect").

### Other_requirements
- Include shift requirements, travel expectations, special eligibility, or any other constraints.

### Meta
- jd_source_filename: set to JD_FILE_NAME value.
- seniority_hints: capture explicit seniority words like "Senior", "Lead", "Architect", "Principal" if present.
- locations_raw: raw location mentions before normalization.
- skills_raw: raw skill tokens as they appear in the JD before normalization.

-----------------------------------------
VALIDATION
-----------------------------------------
- Ensure valid JSON (no comments, no trailing commas).
- All required top-level keys MUST be present even if null/[].
- Do not include any fields not listed in the schema.
- Ignore boilerplate (EOE statements, company benefits, legal disclaimers).

-----------------------------------------
RETURN ONLY THE JSON OBJECT — NOTHING ELSE
-----------------------------------------
