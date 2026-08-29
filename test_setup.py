from dotenv import load_dotenv
import os

load_dotenv(os.path.join(os.path.dirname(__file__), "agent", ".env"))
key = os.getenv("OPENROUTER_API_KEY", "NOT FOUND")

if key == "NOT FOUND":
    print("ERROR: API key not found. Check your .env file.")
else:
    print(f"Key loaded: {key[:15]}...")
    print("Setup is correct.")
