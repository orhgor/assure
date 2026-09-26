"""Frozen synthetic case generators for the Parsure benchmark (``bench-v1``).

Every generator in :mod:`bench.cases.generators` is named in
``bench/manifest.json`` and builds one input file with PyMuPDF from the
wordings in :mod:`bench.cases.texts`. The wordings, the rendering
parameters and the manifest are frozen together: changing a wording or a
DPI changes what the benchmark measures, so it is a new benchmark version
(``bench/README.md``, governance rules).
"""
