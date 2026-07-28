# Official TeleLogs Test Split

This directory contains the 864-record official TeleLogs evaluation split used
only for held-out analysis and benchmarking. It must never be included in SFT,
preference-training, prompt-selection, or training-time data generation.

`test.json` was copied from the immutable Hugging Face dataset snapshot used by
the official Inspect evaluation on the H200 server. The v13 preflight verified
zero normalized-question overlap between this split and the 2,400 training
records.

SHA-256: `d85df56b86c8fcf608583e8508e2d5a99466d3223d8f886d548867305c570099`

The dataset terms prohibit public sharing, re-uploading, and redistribution of
this benchmark data. These files may remain in this Git repository only while
the repository is private.
