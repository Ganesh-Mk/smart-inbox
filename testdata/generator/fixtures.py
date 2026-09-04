"""The fictional universe the whole corpus is built from.

**No real patient data, ever** (CLAUDE.md constraint 2). Every name, drug, hospital and address
here is invented. The drug names in particular are deliberately implausible as real products —
they follow INN-ish morphology so the documents read naturally, but none of them exists, and a
reviewer can confirm that in seconds.

Everything is drawn through a seeded RNG in `build.py`, so the corpus is byte-reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

SYNTHETIC_NOTICE = (
    "SYNTHETIC TEST DOCUMENT — generated for the Smart Inbox prototype. "
    "No real patient, reporter, product or event is described."
)


# --------------------------------------------------------------------------------------
# Invented medicinal products. Each carries the details a form or narrative would mention.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Product:
    name: str
    inn: str
    strength: str
    form: str
    route: str
    indication: str
    batch_prefix: str


PRODUCTS: tuple[Product, ...] = (
    Product("Velmoradine", "velmoradine hydrochloride", "20 mg", "film-coated tablet",
            "ORAL", "essential hypertension", "VLM"),
    Product("Cardexatine", "cardexatine mesilate", "5 mg", "prolonged-release tablet",
            "ORAL", "chronic heart failure", "CDX"),
    Product("Nuvexoral", "nuvexoral sodium", "250 mg/5 mL", "oral suspension",
            "ORAL", "community-acquired pneumonia", "NVX"),
    Product("Zorbitran", "zorbitran acetate", "40 mg/mL", "solution for injection",
            "SUBCUTANEOUS", "rheumatoid arthritis", "ZBT"),
    Product("Pralextin", "pralextin", "100 micrograms/dose", "pressurised inhalation",
            "INHALATION", "asthma maintenance", "PLX"),
    Product("Domitrelle", "domitrelle succinate", "2 mg", "gastro-resistant capsule",
            "ORAL", "generalised anxiety disorder", "DMT"),
    Product("Fenaquil", "fenaquil dipotassium", "500 mg", "effervescent tablet",
            "ORAL", "acute migraine", "FNQ"),
    Product("Astelvia", "astelvia trometamol", "1 g", "powder for infusion",
            "IV", "post-operative infection prophylaxis", "AST"),
)

PRODUCTS_BY_NAME = {p.name: p for p in PRODUCTS}


# --------------------------------------------------------------------------------------
# Adverse events, with the seriousness criterion each one is written to imply.
# The criteria are the six regulatory ones (PROJECT_PLAN §4.2) — never a severity slider.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reaction:
    term: str
    description: str
    serious_criterion: str | None
    outcome: str


REACTIONS: tuple[Reaction, ...] = (
    Reaction("maculopapular rash",
             "a widespread itchy maculopapular rash across the trunk and both forearms",
             None, "RECOVERING"),
    Reaction("angioedema",
             "swelling of the lips and tongue with difficulty swallowing",
             "LIFE_THREATENING", "RECOVERED"),
    Reaction("acute hepatitis",
             "jaundice with alanine aminotransferase raised to 640 U/L",
             "HOSPITALISATION_OR_PROLONGATION", "NOT_RECOVERED"),
    Reaction("syncope",
             "a sudden loss of consciousness while standing, with a fall",
             "HOSPITALISATION_OR_PROLONGATION", "RECOVERED"),
    Reaction("agranulocytosis",
             "a neutrophil count of 0.3 x 10^9/L with fever and mouth ulceration",
             "LIFE_THREATENING", "RECOVERING"),
    Reaction("fatal arrhythmia",
             "a ventricular arrhythmia which did not respond to resuscitation",
             "DEATH", "FATAL"),
    Reaction("persistent peripheral neuropathy",
             "numbness and burning in both feet which has not resolved after six months",
             "DISABILITY_OR_INCAPACITY", "NOT_RECOVERED"),
    Reaction("severe nausea and vomiting",
             "unrelenting nausea with vomiting more than ten times a day",
             "OTHER_MEDICALLY_IMPORTANT", "RECOVERED"),
    Reaction("photosensitivity reaction",
             "an erythematous eruption on sun-exposed skin after ten minutes outdoors",
             None, "RECOVERED"),
    Reaction("dizziness",
             "light-headedness on standing, worse in the mornings",
             None, "RECOVERING"),
)


# --------------------------------------------------------------------------------------
# Reporters. Role matters: it is one of the four ICSR minimum elements (§4.3).
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reporter:
    name: str
    role: str
    email: str
    organisation: str
    country: str
    qualification: str


REPORTERS: tuple[Reporter, ...] = (
    Reporter("Dr Aoife Whitfield", "PHYSICIAN", "a.whitfield@northgate-clinic.example",
             "Northgate Community Clinic", "United Kingdom", "General Practitioner"),
    Reporter("Marcus Delane", "PHARMACIST", "m.delane@harbourside-pharmacy.example",
             "Harbourside Pharmacy", "Ireland", "Community Pharmacist"),
    Reporter("Sister Priya Raghunathan", "NURSE", "p.raghunathan@st-elwyn.example",
             "St Elwyn's General Hospital", "United Kingdom", "Senior Staff Nurse"),
    Reporter("Tomas Berhane", "PATIENT", "tberhane@examplemail.example",
             "", "Netherlands", ""),
    Reporter("Greta Lindqvist", "CONSUMER", "g.lindqvist@examplemail.example",
             "", "Sweden", ""),
    Reporter("Dr Rupert Ashgrove", "PHYSICIAN", "r.ashgrove@meadowvale-hosp.example",
             "Meadowvale District Hospital", "United Kingdom", "Consultant Hepatologist"),
    Reporter("Dr Ingrid Halvorsen", "OTHER_HCP", "i.halvorsen@fjordklinikk.example",
             "Fjordklinikken", "Norway", "Clinical Pharmacologist"),
)


# --------------------------------------------------------------------------------------
# Patients. Descriptors are deliberately varied so the extraction has to cope with
# "58-year-old female", a date of birth, "elderly", and an age in weeks (E30).
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Patient:
    descriptor: str
    age_raw: str
    age_value: str | None
    age_unit: str | None
    sex: str
    initials: str
    weight: str | None
    medical_history: str


PATIENTS: tuple[Patient, ...] = (
    Patient("a 58-year-old female", "58-year-old", "58", "YEAR", "FEMALE", "A.M.",
            "68 kg", "hypertension, type 2 diabetes"),
    Patient("a 34-year-old man", "34-year-old", "34", "YEAR", "MALE", "T.B.",
            "81 kg", "no significant past medical history"),
    Patient("an elderly gentleman", "elderly", None, None, "MALE", "R.K.",
            None, "atrial fibrillation, mild renal impairment"),
    Patient("a 6-week-old infant", "6-week-old", "6", "WEEK", "FEMALE", "L.O.",
            "4.1 kg", "born at 38 weeks, otherwise well"),
    Patient("a woman born in 1966", "born in 1966", None, None, "FEMALE", "S.V.",
            "72 kg", "migraine with aura"),
    Patient("a 71-year-old female", "71-year-old", "71", "YEAR", "FEMALE", "M.D.",
            "59 kg", "osteoarthritis, hypothyroidism"),
    Patient("a 45-year-old male", "45-year-old", "45", "YEAR", "MALE", "K.J.",
            "94 kg", "asthma since childhood"),
    Patient("a 29-year-old pregnant woman", "29-year-old", "29", "YEAR", "FEMALE", "N.P.",
            "70 kg", "first pregnancy, 22 weeks gestation"),
    Patient("a 62-year-old male", "62-year-old", "62", "YEAR", "MALE", "H.G.",
            "78 kg", "chronic kidney disease stage 3"),
    Patient("a 3-year-old boy", "3 y.o.", "3", "YEAR", "MALE", "D.F.",
            "14 kg", "recurrent otitis media"),
)


# --------------------------------------------------------------------------------------
# Product quality defects (PQC). A defect is physical — never dissatisfaction with efficacy.
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Defect:
    summary: str
    detail: str
    category: str


DEFECTS: tuple[Defect, ...] = (
    Defect("broken tamper seal",
           "the outer tamper-evident seal was already split when the carton was opened, and the "
           "blister foil underneath was creased and partly lifted",
           "PACKAGING"),
    Defect("particulate contamination",
           "three of the vials contain visible dark fibrous particles suspended in the solution, "
           "clearly visible when held against a white background",
           "CONTAMINATION"),
    Defect("damaged packaging in transit",
           "the shipping carton arrived crushed on one corner and four of the twelve cartons "
           "inside were torn open, with two blisters punctured",
           "PACKAGING"),
    Defect("discoloured tablets",
           "the tablets in the second blister strip are a distinctly darker brown than the first "
           "strip from the same carton, and one is cracked across the score line",
           "APPEARANCE"),
    Defect("incorrect tablet count",
           "the bottle is labelled as containing 60 tablets but contained only 47 on opening; the "
           "seal was intact",
           "COUNT"),
)


# --------------------------------------------------------------------------------------
# Medical-information questions. A genuine question, no reaction, no defect.
# --------------------------------------------------------------------------------------
MI_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("dosing in renal impairment",
     "Could you tell me whether the dose needs to be reduced in a patient with an eGFR of "
     "28 mL/min/1.73m2, and if so what reduction you would recommend?"),
    ("storage temperature",
     "One of my patients left the pack in a car overnight and it may have reached 35 C. Is the "
     "product still usable, and what is the approved storage range?"),
    ("drug interaction",
     "Is there a clinically significant interaction with clarithromycin? I have a patient who "
     "needs a course for a chest infection."),
    ("use in lactation",
     "Is it considered compatible with breastfeeding, and is there any published measurement of "
     "how much appears in breast milk?"),
    ("administration technique",
     "Should the tablet be swallowed whole or can it be dispersed for a patient with a "
     "percutaneous gastrostomy?"),
)


# --------------------------------------------------------------------------------------
# Places and identifiers used across forms.
# --------------------------------------------------------------------------------------
HOSPITALS: tuple[str, ...] = (
    "St Elwyn's General Hospital, Kirkmoor",
    "Meadowvale District Hospital, Ashby Bassett",
    "Northgate Community Clinic, Pellingford",
    "Fjordklinikken, Storhavn",
    "Harbourside Health Centre, Cairnwell",
)

MARKETING_AUTHORISATION_HOLDER = "Coreline Pharmaceuticals Ltd, Unit 7 Fenway Court, Bramwich"

SAFETY_MAILBOX = "safety@smart-inbox.test"
