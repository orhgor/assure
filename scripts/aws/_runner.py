"""Run a probe file with the app's environment loaded (the same .env the service uses)."""
import os, runpy, sys

try:
    from dotenv import load_dotenv
    for candidate in (".env.staging", ".env.production", ".env"):
        if os.path.exists(candidate):
            load_dotenv(candidate)
            break
except Exception as exc:  # pragma: no cover
    print("dotenv unavailable:", exc)

target = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(target, run_name="__main__")
