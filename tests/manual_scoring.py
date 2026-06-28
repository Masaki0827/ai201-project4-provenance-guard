from app import create_app
import json
import tempfile

tmp = tempfile.NamedTemporaryFile(prefix="pg_test_", suffix=".db", delete=False)
db_path = tmp.name
tmp.close()

app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
client = app.test_client()

inputs = [
    # Clearly AI-generated
    "Artificial intelligence represents a transformative paradigm shift in modern society.\nIt is important to note that while the benefits of AI are numerous, it is equally\nessential to consider the ethical implications. Furthermore, stakeholders across\nvarious sectors must collaborate to ensure responsible deployment.",

    # Clearly human-written
    "ok so i finally tried that new ramen place downtown and honestly?\nunderwhelming. the broth was fine but they put WAY too much sodium in it and\ni was thirsty for like three hours after. my friend got the spicy version and\nsaid it was better. probably won't go back unless someone drags me there",

    # Borderline: formal human writing
    "The relationship between monetary policy and asset price inflation has been\nextensively studied in the literature. Central banks face a fundamental tension\nbetween their mandate for price stability and the unintended consequences of\nprolonged low interest rates on equity and real estate valuations.",

    # Borderline: lightly edited AI output
    "I've been thinking a lot about remote work lately. There are genuine tradeoffs —\nflexibility and no commute on one side, isolation and blurred work-life boundaries\non the other. Studies show productivity varies widely by individual and role type."
]

for i, text in enumerate(inputs, start=1):
    resp = client.post("/submit", json={"text": text, "creator_id": f"tester-{i}"})
    print(f"--- Input {i} ---")
    print(json.dumps(resp.get_json(), indent=2, ensure_ascii=False))

# Also print last few audit log entries
print("--- Audit log ---")
log = client.get('/log')
print(json.dumps(log.get_json()[:6], indent=2, ensure_ascii=False))
