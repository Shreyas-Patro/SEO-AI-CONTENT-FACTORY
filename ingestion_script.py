from ingestion.pipeline import ingest_document

# 🔧 EDIT THESE
FILE_PATH = r"C:\Users\Shrey\OneDrive\Documents\Brigade Group in Bengaluru 2026 Res.txt"  # put your file path here
TOPIC = "Brigade"     # optional
SOURCE_URL = ""                     # optional


def main():
    print(f"\n📄 Ingesting: {FILE_PATH}")
    if TOPIC:
        print(f"🏷️ Topic: {TOPIC}")

    result = ingest_document(
        filepath=FILE_PATH,
        topic=TOPIC,
        source_url=SOURCE_URL
    )

    print("\n✅ Ingestion Complete!\n")
    print("Summary:\n")

    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()