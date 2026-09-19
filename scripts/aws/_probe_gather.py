"""Run the window verification over a draft and print the moved paragraphs."""
import sys, io, contextlib
code = open("/home/ubuntu/probes/_probe_window_verify.py").read()
sys.argv = sys.argv
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    exec(compile(code, "wv", "exec"), {"__name__": "__main__"})
out = buf.getvalue()
blocks = out.split("\n== ")
for b in blocks[1:]:
    lines = b.splitlines()
    single = [l for l in lines if l.strip().startswith("single:")]
    window = [l for l in lines if l.strip().startswith("window:")]
    if single and window and "PASS" in window[0] and "PASS" not in single[0]:
        print("== " + b)
