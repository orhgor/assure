"""Aggregate: every paragraph in a set of real drafts where a 1-sentence candidate
failed the floors but a 2-3 sentence window clears them."""
import sys
sys.path.insert(0, "/home/ubuntu/probes")
import importlib.util
spec = importlib.util.spec_from_file_location("wv", "/home/ubuntu/probes/_probe_window_verify.py")
