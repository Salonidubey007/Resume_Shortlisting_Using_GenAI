# utils/schema_validator.py

def validate_resume_schema(obj: dict) -> dict:
    """
    Ensures keys for resume JSON are present.
    """
    REQUIRED = [
        "Candidate_Emp_ID", "Candidate_name", "Current_Role",
        "Candidate_grade", "Profile_location", "Domains_known",
        "Total_experience_years", "Tech_skillset", "Functional_skillset",
        "Overall_responsibilities", "Count_of_projects",
        "Current_project_age", "Previously_worked_for_the_customer",
        "Source_file"
    ]

    for k in REQUIRED:
        if k not in obj:
            obj[k] = None

    return obj


def validate_jd_schema(obj: dict) -> dict:
    REQUIRED = [
        "Role_name", "Grade", "Account_name", "Business_Domain",
        "Work_location", "Experience_band", "Mandatory_tech_skills",
        "Nice_to_have_tech_skills", "Soft_skills", "Responsibilities",
        "Education", "Certifications", "Other_requirements", "Meta"
    ]

    for k in REQUIRED:
        if k not in obj:
            obj[k] = None
    if "history" in obj and not isinstance(obj["history"], list):
        obj["history"] = []

    return obj
